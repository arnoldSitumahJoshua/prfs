from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin

db = SQLAlchemy()

# ============================================================
# MASTER DATA
# ============================================================

class Site(db.Model):
    __tablename__ = "sites"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    code = db.Column(db.String(10), unique=True, nullable=False)
    location = db.Column(db.String(200))
    active = db.Column(db.Boolean, default=True)

class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    username = db.Column(db.String(60), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True)
    phone = db.Column(db.String(30))
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.Enum("site", "officer", "viewer"), nullable=False, default="site")
    site_id = db.Column(db.Integer, db.ForeignKey("sites.id"))
    active = db.Column(db.Boolean, default=True)
    last_login = db.Column(db.DateTime)
    failed_attempts = db.Column(db.Integer, default=0)
    locked_until = db.Column(db.DateTime)
    must_change_password = db.Column(db.Boolean, default=False)

    site = db.relationship("Site")

class Category(db.Model):
    __tablename__ = "categories"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey("categories.id"))

class Unit(db.Model):
    __tablename__ = "units"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(30), nullable=False)
    symbol = db.Column(db.String(10))
    allows_decimal = db.Column(db.Boolean, default=False)

class Item(db.Model):
    __tablename__ = "items"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), unique=True, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"))
    unit_id = db.Column(db.Integer, db.ForeignKey("units.id"))
    keywords = db.Column(db.String(255))
    image_filename = db.Column(db.String(255))
    price = db.Column(db.Numeric(12, 2), default=0.00)
    accounting_code = db.Column(db.String(50), unique=True, nullable=True) 
    active = db.Column(db.Boolean, default=True)

    category = db.relationship("Category")
    unit = db.relationship("Unit")

class Supplier(db.Model):
    __tablename__ = "suppliers"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    contact = db.Column(db.String(120))
    phone = db.Column(db.String(30))
    email = db.Column(db.String(120))
    tax_pin = db.Column(db.String(40))

# ============================================================
# PURCHASE ORDERS
# ============================================================

class PO(db.Model):
    __tablename__ = "pos"
    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(40), unique=True, nullable=False)
    site_id = db.Column(db.Integer, db.ForeignKey("sites.id"), nullable=False)
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"))
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    po_date = db.Column(db.Date, default=datetime.utcnow)
    expected_date = db.Column(db.Date)
    
    status = db.Column(db.String(30), default="Draft")
    dispatch_date = db.Column(db.Date)
    delivery_note_ref = db.Column(db.String(100))
    tracking_ref = db.Column(db.String(100))
    
    remarks = db.Column(db.Text)
    cancel_reason = db.Column(db.Text)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    site = db.relationship("Site")
    supplier = db.relationship("Supplier")
    officer = db.relationship("User", foreign_keys=[created_by])
    
    lines = db.relationship("POLine", backref="po", cascade="all, delete-orphan")
    attachments = db.relationship("POAttachment", backref="po", cascade="all, delete-orphan")
    receipts = db.relationship("DeliveryReceipt", backref="po", cascade="all, delete-orphan")

class POLine(db.Model):
    __tablename__ = "po_lines"
    id = db.Column(db.Integer, primary_key=True)
    po_id = db.Column(db.Integer, db.ForeignKey("pos.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("items.id"), nullable=False)
    ordered_qty = db.Column(db.Numeric(12, 3), nullable=False)
    received_qty = db.Column(db.Numeric(12, 3), default=0)
    unit_price = db.Column(db.Numeric(12, 2), default=0.00)
    remarks = db.Column(db.String(255))

    item = db.relationship("Item")

    @property
    def outstanding_qty(self):
        return float(self.ordered_qty or 0) - float(self.received_qty or 0)
    
    @property
    def shipped_qty(self):
        return sum(float(s.qty or 0) for s in self.shipment_lines)

    @property
    def remaining_to_ship(self):
        return max(0.0, float(self.ordered_qty or 0) - self.shipped_qty)

class Shipment(db.Model):
    __tablename__ = "shipments"
    id = db.Column(db.Integer, primary_key=True)
    po_id = db.Column(db.Integer, db.ForeignKey("pos.id"), nullable=False)
    dispatch_date = db.Column(db.Date)
    delivery_note_ref = db.Column(db.String(100))
    tracking_ref = db.Column(db.String(100))
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    po = db.relationship("PO", backref=db.backref("shipments", order_by="Shipment.id"))
    lines = db.relationship("ShipmentLine", backref="shipment", cascade="all, delete-orphan")


class ShipmentLine(db.Model):
    __tablename__ = "shipment_lines"
    id = db.Column(db.Integer, primary_key=True)
    shipment_id = db.Column(db.Integer, db.ForeignKey("shipments.id"), nullable=False)
    po_line_id = db.Column(db.Integer, db.ForeignKey("po_lines.id"), nullable=False)
    qty = db.Column(db.Numeric(12, 3), nullable=False)

    po_line = db.relationship("POLine", backref="shipment_lines")

class POAttachment(db.Model):
    __tablename__ = "po_attachments"
    id = db.Column(db.Integer, primary_key=True)
    po_id = db.Column(db.Integer, db.ForeignKey("pos.id"), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    stored_name = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(40), default="PO")
    size = db.Column(db.Integer)
    uploader_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    deleted = db.Column(db.Boolean, default=False)

    uploader = db.relationship("User")

# ============================================================
# DELIVERY RECEIPTS
# ============================================================

class DeliveryReceipt(db.Model):
    __tablename__ = "delivery_receipts"
    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(30), unique=True, nullable=False)
    po_id = db.Column(db.Integer, db.ForeignKey("pos.id"), nullable=False)
    site_id = db.Column(db.Integer, db.ForeignKey("sites.id"))
    received_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    received_at = db.Column(db.DateTime, default=datetime.utcnow)
    delivery_note_ref = db.Column(db.String(100))
    notes = db.Column(db.Text)

    lines = db.relationship("DRLine", backref="dr", cascade="all, delete-orphan")
    received_by_user = db.relationship("User", foreign_keys=[received_by])

class DRLine(db.Model):
    __tablename__ = "dr_lines"
    id = db.Column(db.Integer, primary_key=True)
    dr_id = db.Column(db.Integer, db.ForeignKey("delivery_receipts.id"), nullable=False)
    po_line_id = db.Column(db.Integer, db.ForeignKey("po_lines.id"), nullable=False)
    qty_received = db.Column(db.Numeric(12, 3), nullable=False)
    qty_rejected = db.Column(db.Numeric(12, 3), default=0)
    reject_reason = db.Column(db.String(255))

    po_line = db.relationship("POLine")

# ============================================================
# ADMIN / HISTORY
# ============================================================

class StatusHistory(db.Model):
    __tablename__ = "status_history"
    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(db.String(30))
    entity_id = db.Column(db.Integer)
    from_status = db.Column(db.String(30))
    to_status = db.Column(db.String(30))
    by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    at = db.Column(db.DateTime, default=datetime.utcnow)
    remark = db.Column(db.String(255))

    user = db.relationship("User")

class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    role = db.Column(db.String(30))
    site_id = db.Column(db.Integer)
    action = db.Column(db.String(80))
    entity = db.Column(db.String(80))
    entity_id = db.Column(db.Integer)
    old_value = db.Column(db.Text)
    new_value = db.Column(db.Text)
    ip = db.Column(db.String(45))
    at = db.Column(db.DateTime, default=datetime.utcnow)

class Notification(db.Model):
    __tablename__ = "notifications"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    kind = db.Column(db.String(40), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.String(500))
    link = db.Column(db.String(255))
    read = db.Column(db.Boolean, default=False)
    at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User")

class SystemSetting(db.Model):
    __tablename__ = "system_settings"
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(80), unique=True, nullable=False)
    value = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class EmailLog(db.Model):
    __tablename__ = "email_logs"
    id = db.Column(db.Integer, primary_key=True)
    po_id = db.Column(db.Integer, db.ForeignKey("pos.id"))
    recipient = db.Column(db.String(255))
    subject = db.Column(db.String(255))
    status = db.Column(db.String(20), default="queued")
    attempts = db.Column(db.Integer, default=0)
    detail = db.Column(db.Text)
    at = db.Column(db.DateTime, default=datetime.utcnow)