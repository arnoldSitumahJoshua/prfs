"""
PRFS — Procurement Requisition & Fulfilment System
New direct-order workflow with full Admin tools.
"""

import os
import secrets
from datetime import datetime, date, timedelta

from flask import (Flask, render_template, request, redirect, url_for,
                   flash, send_file, abort, jsonify, session)
from flask_login import (LoginManager, login_user, logout_user,
                         login_required, current_user)
from sqlalchemy import or_, func

from config import Config
from models import (db, Site, User, Category, Unit, Item, Supplier,
                    PO, POLine, POAttachment,
                    DeliveryReceipt, DRLine, StatusHistory, AuditLog,
                    Notification, SystemSetting, EmailLog, Shipment, ShipmentLine)
from utils import (hash_password, verify_password, role_required,
                   audit, save_upload, notify, notify_officers)
from passwords import check_password_strength


app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please sign in to continue."
login_manager.login_message_category = "error"


# ============================================================
# HELPERS
# ============================================================
def _get_setting(key, default=""):
    row = SystemSetting.query.filter_by(key=key).first()
    return row.value if row and row.value is not None else default

def _set_setting(key, value):
    row = SystemSetting.query.filter_by(key=key).first()
    if row:
        row.value = value
    else:
        db.session.add(SystemSetting(key=key, value=value))

def _paginate(query, default_size=10):
    per_page = request.args.get("per_page", default_size, type=int)
    page = request.args.get("page", 1, type=int)
    total = query.count()
    items = query.offset((page - 1) * per_page).limit(per_page).all()
    return {
        "items": items, "page": page, "per_page": per_page,
        "total": total, "total_pages": max(1, (total + per_page - 1) // per_page),
        "has_prev": page > 1, "has_next": page < (total + per_page - 1) // per_page
    }

def recalc_po_status(po):
    """Calculate PO status based on received quantities."""
    if po.status in ('Cancelled', 'Completed', 'Draft'):
        return
        
    total_ordered = sum(float(l.ordered_qty) for l in po.lines)
    total_received = sum(float(l.received_qty) for l in po.lines)
    
    if total_received == 0:
        if po.status not in ('Dispatched',):
            po.status = 'Ordered'
    elif total_received < total_ordered:
        po.status = 'Partially Delivered'
    elif total_received >= total_ordered:
        po.status = 'Delivered'


# ============================================================
# AUTH
# ============================================================
@app.route("/login", methods=["GET", "POST"])
def login():
    errors = {}
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()

        if not user or not user.active:
            errors["username"] = "Invalid username or inactive account."
        elif user.locked_until and user.locked_until > datetime.utcnow():
            errors["password"] = "Account locked. Try again later."
        elif not verify_password(user.password_hash, password):
            user.failed_attempts = (user.failed_attempts or 0) + 1
            if user.failed_attempts >= 5:
                user.locked_until = datetime.utcnow() + timedelta(minutes=15)
            db.session.commit()
            errors["password"] = "Incorrect password."
        else:
            user.failed_attempts = 0
            user.locked_until = None
            user.last_login = datetime.utcnow()
            login_user(user)
            audit("LOGIN", "User", user.id, ip=request.remote_addr)
            db.session.commit()
            if user.must_change_password:
                return redirect(url_for("change_password"))
            return redirect(url_for("dashboard"))
    return render_template("auth/login.html", errors=errors)


@app.route("/logout")
@login_required
def logout():
    audit("LOGOUT", "User", current_user.id, ip=request.remote_addr)
    db.session.commit()
    logout_user()
    return redirect(url_for("login"))

@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current = request.form.get("current_password")
        new = request.form.get("new_password")
        if not verify_password(current_user.password_hash, current):
            flash("Current password incorrect.", "error")
            return redirect(url_for("change_password"))
        ok, err = check_password_strength(new, current_user.username)
        if not ok:
            flash(err, "error")
            return redirect(url_for("change_password"))
        current_user.password_hash = hash_password(new)
        current_user.must_change_password = False
        db.session.commit()
        flash("Password updated.", "success")
        return redirect(url_for("dashboard"))
    return render_template("auth/change_password.html")


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

@app.context_processor
def inject_settings():
    return {
        "settings_company": _get_setting("company_name", "Company"),
        "settings_logo": _get_setting("company_logo_text", "PRFS"),
    }

@app.template_filter("eat")
def _eat_filter(dt):
    if not dt: return "—"
    return (dt + timedelta(hours=3)).strftime("%d/%m/%Y %H:%M")


# ============================================================
# DASHBOARD
# ============================================================
@app.route("/")
@login_required
def dashboard():
    if current_user.role in ("officer", "viewer"):
        q = PO.query
        tiles = {
            "Draft Orders": q.filter_by(status="Draft").count(),
            "Awaiting Dispatch": q.filter_by(status="Ordered").count(),
            "Dispatched": q.filter_by(status="Dispatched").count(),
            "Partially Delivered": q.filter_by(status="Partially Delivered").count(),
            "Completed": q.filter_by(status="Completed").count(),
        }
        recent = q.order_by(PO.id.desc()).limit(10).all()
    else:
        q = PO.query.filter_by(site_id=current_user.site_id).filter(PO.status != "Draft")
        tiles = {
            "Incoming Orders": q.filter(PO.status.in_(["Ordered", "Dispatched"])).count(),
            "Partially Delivered": q.filter_by(status="Partially Delivered").count(),
            "Fully Delivered": q.filter_by(status="Delivered").count(),
            "Completed": q.filter_by(status="Completed").count(),
        }
        recent = q.order_by(PO.id.desc()).limit(10).all()

    return render_template("dashboard.html", tiles=tiles, recent=recent)


# ============================================================
# CATALOGUE
# ============================================================
@app.route("/catalogue")
@login_required
def catalogue():
    q = request.args.get("q", "").strip()
    query = Item.query.filter_by(active=True)
    if q:
        query = query.filter(Item.name.ilike(f"%{q}%") | Item.code.ilike(f"%{q}%"))
    pager = _paginate(query.order_by(Item.name))
    return render_template("catalogue/list.html", items=pager["items"], pager=pager, q=q)

@app.route("/api/catalogue/search")
@login_required
def api_catalogue_search():
    q = request.args.get("q", "").strip()
    if len(q) < 2: return jsonify([])
    items = Item.query.filter(Item.active==True, Item.name.ilike(f"%{q}%")).limit(15).all()
    return jsonify([{
        "id": i.id, "code": i.code, "name": i.name,
        "price": float(i.price or 0), "unit": i.unit.name if i.unit else "",
        "allows_decimal": i.unit.allows_decimal if i.unit else False
    } for i in items])


# ============================================================
# PURCHASE ORDERS
# ============================================================
@app.route("/po")
@login_required
def po_list():
    query = PO.query
    if current_user.role == "site":
        query = query.filter_by(site_id=current_user.site_id).filter(PO.status != "Draft")
    
    f_status = request.args.get("status", "").strip()
    if f_status: query = query.filter(PO.status == f_status)
    
    pager = _paginate(query.order_by(PO.id.desc()))
    return render_template("po/list.html", pos=pager["items"], pager=pager, f_status=f_status)


@app.route("/po/new", methods=["GET", "POST"])
@login_required
@role_required("officer")
def po_new():
    if request.method == "POST":
        action = request.form.get("action", "submit")
        
                # ---- read the item lines first ----
        item_ids = request.form.getlist("item_id[]")
        qtys     = request.form.getlist("qty[]")
        prices   = request.form.getlist("price[]")
        remarks  = request.form.getlist("line_remark[]")

        lines = []
        for i, iid in enumerate(item_ids):
            qty = float(qtys[i]) if i < len(qtys) and qtys[i] else 0
            if not iid or qty <= 0:
                continue
            price = float(prices[i]) if i < len(prices) and prices[i] else 0
            rem = remarks[i] if i < len(remarks) else None
            lines.append((int(iid), qty, price, rem))

        if not lines:
            flash("Add at least one item to the order.", "error")
            return redirect(url_for("po_new"))

        # ---- now create the PO ----
        year = datetime.utcnow().year
        count = PO.query.filter(PO.number.like(f"PO-{year}-%")).count() + 1
        po_number = f"PO-{year}-{count:05d}"

        po = PO(
            number=po_number,
            site_id=request.form.get("site_id"),
            supplier_id=request.form.get("supplier_id"),
            created_by=current_user.id,
            po_date=datetime.strptime(request.form.get("po_date"), "%Y-%m-%d").date(),
            expected_date=datetime.strptime(request.form.get("expected_date"), "%Y-%m-%d").date() if request.form.get("expected_date") else None,
            status="Draft" if action == "draft" else "Ordered",
            remarks=request.form.get("remarks")
        )
        db.session.add(po)
        db.session.flush()

        for iid, qty, price, rem in lines:
            db.session.add(POLine(
                po_id=po.id, item_id=iid,
                ordered_qty=qty, unit_price=price,
                remarks=rem
            ))

        if action != "draft" and po.site:
            for u in User.query.filter_by(role="site", site_id=po.site_id, active=True):
                notify(u.id, "new_order", f"New Order {po.number} incoming", 
                       f"Supplier: {po.supplier.name}", url_for("po_detail", po_id=po.id))

        audit("CREATE_PO", "PO", po.id, new=po.number, ip=request.remote_addr)
        db.session.commit()
        
        flash(f"Purchase Order {po.number} created as {po.status}.", "success")
        return redirect(url_for("po_detail", po_id=po.id))

    return render_template("po/new.html", 
                           sites=Site.query.filter_by(active=True).all(),
                           suppliers=Supplier.query.all(),
                           today=date.today().isoformat())


@app.route("/po/<int:po_id>")
@login_required
def po_detail(po_id):
    po = db.session.get(PO, po_id) or abort(404)
    if current_user.role == "site" and po.site_id != current_user.site_id:
        abort(403)
    if current_user.role == "site" and po.status == "Draft":
        abort(404)
    return render_template("po/detail.html", po=po)


@app.route("/po/<int:po_id>/dispatch", methods=["POST"])
@login_required
@role_required("officer")
def po_dispatch(po_id):
    """Record one shipment (any number of shipments per order)."""
    po = db.session.get(PO, po_id) or abort(404)
    if po.status not in ("Ordered", "Dispatched", "Partially Delivered"):
        flash("Shipments can only be recorded on an open order.", "error")
        return redirect(url_for("po_detail", po_id=po.id))

    entries = []
    for line in po.lines:
        raw = (request.form.get(f"ship_{line.id}") or "").strip()
        try:
            qty = float(raw) if raw else 0
        except ValueError:
            flash(f"Invalid quantity for {line.item.name}.", "error")
            return redirect(url_for("po_detail", po_id=po.id))
        if qty <= 0:
            continue
        if qty > line.remaining_to_ship + 1e-9:
            flash(f"{line.item.name}: only {line.remaining_to_ship:g} left to ship.", "error")
            return redirect(url_for("po_detail", po_id=po.id))
        entries.append((line, qty))

    if not entries:
        flash("Enter the quantity shipped for at least one item.", "error")
        return redirect(url_for("po_detail", po_id=po.id))

    old_status = po.status
    try:
        d_date = datetime.strptime(request.form.get("dispatch_date", ""), "%Y-%m-%d").date()
    except ValueError:
        d_date = date.today()

    shipment = Shipment(
        po_id=po.id, dispatch_date=d_date,
        delivery_note_ref=(request.form.get("delivery_note_ref") or "").strip() or None,
        tracking_ref=(request.form.get("tracking_ref") or "").strip() or None,
        created_by=current_user.id)
    db.session.add(shipment)
    db.session.flush()
    for line, qty in entries:
        db.session.add(ShipmentLine(shipment_id=shipment.id, po_line_id=line.id, qty=qty))

    # keep the latest details on the PO header
    po.dispatch_date = shipment.dispatch_date
    po.delivery_note_ref = shipment.delivery_note_ref
    po.tracking_ref = shipment.tracking_ref
    if po.status == "Ordered":
        po.status = "Dispatched"

    db.session.add(StatusHistory(entity_type="PO", entity_id=po.id,
                                 from_status=old_status, to_status=po.status,
                                 by_user_id=current_user.id))

    summary = ", ".join(f"{q:g} × {l.item.name}" for l, q in entries)
    for u in User.query.filter_by(role="site", site_id=po.site_id, active=True):
        notify(u.id, "dispatched", f"Shipment on its way — {po.number}",
               summary, url_for("po_detail", po_id=po.id))

    db.session.commit()
    flash(f"Shipment recorded: {summary}.", "success")
    return redirect(url_for("po_detail", po_id=po.id))

@app.route("/po/<int:po_id>/complete", methods=["POST"])
@login_required
@role_required("officer")
def po_complete(po_id):
    po = db.session.get(PO, po_id) or abort(404)
    po.status = "Completed"
    db.session.add(StatusHistory(entity_type="PO", entity_id=po.id, from_status=po.status, to_status="Completed", by_user_id=current_user.id))
    db.session.commit()
    flash("Order formally closed and marked Completed.", "success")
    return redirect(url_for("po_detail", po_id=po.id))


@app.route("/po/<int:po_id>/cancel", methods=["POST"])
@login_required
@role_required("officer")
def po_cancel(po_id):
    po = db.session.get(PO, po_id) or abort(404)
    reason = request.form.get("reason")
    if not reason:
        flash("Reason required to cancel.", "error")
        return redirect(url_for("po_detail", po_id=po.id))
    
    po.status = "Cancelled"
    po.cancel_reason = reason
    db.session.commit()
    flash("Order Cancelled.", "success")
    return redirect(url_for("po_detail", po_id=po.id))


# ============================================================
# DELIVERY CONFIRMATION
# ============================================================
@app.route("/po/<int:po_id>/receive", methods=["GET", "POST"])
@login_required
@role_required("site")
def receive_delivery(po_id):
    po = db.session.get(PO, po_id) or abort(404)
    if po.site_id != current_user.site_id:
        abort(403)
    if po.status not in ("Ordered", "Dispatched", "Partially Delivered"):
        flash("This order cannot receive more items.", "error")
        return redirect(url_for("po_detail", po_id=po.id))

    if request.method == "POST":
        count = DeliveryReceipt.query.filter_by(po_id=po.id).count() + 1
        dr = DeliveryReceipt(
            number=f"DR-{po.number}-{count:02d}",
            po_id=po.id, site_id=po.site_id,
            received_by=current_user.id,
            delivery_note_ref=request.form.get("delivery_note_ref"),
            notes=request.form.get("notes")
        )
        db.session.add(dr)
        db.session.flush()

        any_received = False
        for line in po.lines:
            qty_in = request.form.get(f"received_{line.id}")
            qty_rej = request.form.get(f"rejected_{line.id}")
            reason = request.form.get(f"reason_{line.id}")

            if qty_in:
                q = float(qty_in)
                r = float(qty_rej) if qty_rej else 0
                if q > 0 or r > 0:
                    line.received_qty = float(line.received_qty or 0) + (q - r)
                    db.session.add(DRLine(
                        dr_id=dr.id, po_line_id=line.id,
                        qty_received=q, qty_rejected=r, reject_reason=reason
                    ))
                    any_received = True

        if any_received:
            recalc_po_status(po)
            db.session.add(StatusHistory(entity_type="PO", entity_id=po.id, from_status="Dispatched", to_status=po.status, by_user_id=current_user.id))
            
            for u in User.query.filter_by(role="officer", active=True):
                notify(u.id, "delivery", f"Delivery confirmed for {po.number}", 
                       f"{current_user.name} received items.", url_for("po_detail", po_id=po.id))
            
            db.session.commit()
            flash(f"Delivery {dr.number} recorded.", "success")
            return redirect(url_for("po_detail", po_id=po.id))
        else:
            db.session.rollback()
            flash("No quantities entered.", "error")

    return render_template("po/receive.html", po=po, today=date.today().isoformat())


# ============================================================
# ADMIN CATALOGUE
# ============================================================
@app.route("/admin/catalogue/new", methods=["GET", "POST"])
@login_required
@role_required("officer")
def admin_catalogue_new():
    if request.method == "POST":
        code = request.form.get("code", "").strip().upper()
        if not code:
            count = Item.query.count() + 1
            code = f"PRD-{count:05d}"
            
        if Item.query.filter_by(code=code).first():
            flash(f"Item code {code} exists.", "error")
            return redirect(url_for("admin_catalogue_new"))

        item = Item(
            code=code,
            name=request.form.get("name"),
            price=request.form.get("price", 0),
            accounting_code=request.form.get("accounting_code"),
            category_id=request.form.get("category_id"),
            unit_id=request.form.get("unit_id"),
            description=request.form.get("description")
        )
        db.session.add(item)
        db.session.commit()
        flash(f"Product {item.name} created with code {item.code}.", "success")
        return redirect(url_for("admin_catalogue"))

    return render_template("admin/catalogue_form.html", 
                           item=None, 
                           categories=Category.query.all(), 
                           units=Unit.query.all())

@app.route("/admin/catalogue/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("officer")
def admin_catalogue_edit(item_id):
    item = db.session.get(Item, item_id) or abort(404)
    if request.method == "POST":
        item.name = request.form.get("name", item.name).strip()
        item.price = request.form.get("price", item.price)
        item.description = request.form.get("description")
        item.category_id = request.form.get("category_id")
        item.unit_id = request.form.get("unit_id")
        db.session.commit()
        flash(f"Item '{item.code}' updated.", "success")
        return redirect(url_for("admin_catalogue"))

    return render_template("admin/catalogue_form.html", 
                           item=item, 
                           categories=Category.query.all(), 
                           units=Unit.query.all())

@app.route("/admin/catalogue/<int:item_id>/toggle", methods=["POST"])
@login_required
@role_required("officer")
def admin_catalogue_toggle(item_id):
    item = db.session.get(Item, item_id) or abort(404)
    item.active = not item.active
    db.session.commit()
    flash(f"Item '{item.code}' is now {'active' if item.active else 'inactive'}.", "success")
    return redirect(url_for("admin_catalogue"))

@app.route("/admin/catalogue")
@login_required
@role_required("officer")
def admin_catalogue():
    pager = _paginate(Item.query.order_by(Item.id.desc()))
    return render_template("admin/catalogue.html", items=pager["items"], pager=pager)


# ============================================================
# ADMIN SUPPLIERS
# ============================================================
@app.route("/suppliers", methods=["GET", "POST"])
@login_required
@role_required("officer", "viewer")
def suppliers_list():
    if request.method == "POST" and current_user.role == "officer":
        name = request.form.get("name")
        if name:
            db.session.add(Supplier(name=name, contact=request.form.get("contact"), phone=request.form.get("phone")))
            db.session.commit()
            flash("Supplier added.", "success")
            return redirect(url_for("suppliers_list"))
    pager = _paginate(Supplier.query.order_by(Supplier.name))
    return render_template("suppliers/list.html", suppliers=pager["items"], pager=pager)


# ============================================================
# ADMIN SETTINGS & USERS & AUDIT
# ============================================================
@app.route("/admin/users", methods=["GET", "POST"])
@login_required
@role_required("officer")
def admin_users():
    if request.method == "POST":
        username = request.form["username"].strip().lower()
        if not User.query.filter_by(username=username).first():
            u = User(
                name=request.form["name"], username=username,
                role=request.form["role"], site_id=request.form.get("site_id"),
                password_hash=hash_password(request.form["password"])
            )
            db.session.add(u)
            db.session.commit()
            flash("User created.", "success")
    pager = _paginate(User.query.order_by(User.role, User.username))
    return render_template("admin/users.html", users=pager["items"], pager=pager, sites=Site.query.all(), now=datetime.utcnow())

@app.route("/admin/users/<int:user_id>/toggle", methods=["POST"])
@login_required
@role_required("officer")
def admin_user_toggle(user_id):
    u = db.session.get(User, user_id) or abort(404)
    if u.id == current_user.id:
        flash("You cannot deactivate your own account.", "error")
    else:
        u.active = not u.active
        db.session.commit()
        flash(f"User '{u.username}' is now {'active' if u.active else 'inactive'}.", "success")
    return redirect(url_for("admin_users"))

@app.route("/admin/users/<int:user_id>/unlock", methods=["POST"])
@login_required
@role_required("officer")
def admin_user_unlock(user_id):
    u = db.session.get(User, user_id) or abort(404)
    u.failed_attempts = 0
    u.locked_until = None
    db.session.commit()
    flash(f"User '{u.username}' unlocked.", "success")
    return redirect(url_for("admin_users"))

@app.route("/admin/audit")
@login_required
@role_required("officer")
def admin_audit():
    pager = _paginate(AuditLog.query.order_by(AuditLog.id.desc()))
    return render_template("admin/audit.html", logs=pager["items"], pager=pager)

@app.route("/admin/settings", methods=["GET", "POST"])
@login_required
@role_required("officer")
def admin_settings():
    if request.method == "POST":
        section = request.form.get("section", "")
        for k, v in request.form.items():
            if k not in ('section', 'csrf_token'):
                _set_setting(k, v.strip())
        db.session.commit()
        flash("Settings saved.", "success")
        return redirect(url_for("admin_settings", tab=section))

    class _SettingsDict(dict):
        def get(self, key, default=None): return dict.get(self, key, default)

    settings = _SettingsDict({row.key: row.value for row in SystemSetting.query.all()})
    return render_template("admin/settings.html", settings=settings, tab=request.args.get("tab", "company"))


# ============================================================
# NOTIFICATIONS
# ============================================================
@app.route("/api/notifications")
@login_required
def api_notifications():
    unread = Notification.query.filter_by(user_id=current_user.id, read=False).count()
    recent = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.id.desc()).limit(10).all()
    return jsonify({
        "unread": unread,
        "items": [{
            "id": n.id, "title": n.title, "body": n.body, "link": n.link, "read": n.read,
            "at": (n.at + timedelta(hours=3)).strftime("%d/%m %H:%M")
        } for n in recent]
    })

@app.route("/api/notifications/<int:nid>/read", methods=["POST"])
@login_required
def api_notification_read(nid):
    n = db.session.get(Notification, nid)
    if n and n.user_id == current_user.id:
        n.read = True
        db.session.commit()
    return jsonify({"ok": True})


if __name__ == "__main__":
    with app.app_context():
        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
        db.create_all()
    app.run(debug=True, host="127.0.0.1", port=5000)