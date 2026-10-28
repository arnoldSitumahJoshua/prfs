from app import app
from models import db, Site, User, Unit, Category, Item, Supplier
from utils import hash_password

def run():
    with app.app_context():
        db.create_all()
        
        # Sites
        sites = [("Canaan Towers", "CTW", "Nairobi"), ("Samat Holdings", "SMT", "Nairobi")]
        for n, c, l in sites:
            if not Site.query.filter_by(code=c).first():
                db.session.add(Site(name=n, code=c, location=l))
        
        # Admin
        if not User.query.filter_by(username="admin").first():
            db.session.add(User(name="Cynthia", username="admin", role="officer", password_hash=hash_password("Admin@12345")))
            
        # Site Manager
        db.session.flush()
        ctw = Site.query.filter_by(code="CTW").first()
        if not User.query.filter_by(username="ghenet").first():
            db.session.add(User(name="Ghenet", username="ghenet", role="site", site_id=ctw.id, password_hash=hash_password("Site@12345")))

        # Catalogue
        if not Unit.query.first():
            db.session.add(Unit(name="Bag", symbol="bag", allows_decimal=False))
            db.session.flush()
            
        u_bag = Unit.query.first()
        if not Item.query.first():
            db.session.add(Item(code="CEM-001", name="Cement 50kg", price=650, unit_id=u_bag.id))
        
        # Supplier
        if not Supplier.query.first():
            db.session.add(Supplier(name="Builders Supply Ltd"))

        db.session.commit()
        print("Database seeded.")

if __name__ == "__main__":
    run()