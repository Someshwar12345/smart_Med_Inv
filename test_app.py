# Automated Verification Script for Smart Medical Inventory System
import os
import sys

# Add current folder to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config import Config
from db_helper import DBHelper
from prediction_helper import PredictionHelper

def verify_system():
    print("================ SYSTEM VERIFICATION STARTED ================")
    
    # 1. Force SQLite mode for verification tests to avoid dependency on a running MySQL server
    Config.DB_TYPE = 'sqlite'
    
    # Check database path
    print(f"Database Type configured: {Config.DB_TYPE}")
    print(f"SQLite DB Path: {Config.SQLITE_DB_PATH}")
    
    # Clean previous SQLite DB if it exists to test schema generation cleanly
    if os.path.exists(Config.SQLITE_DB_PATH):
        try:
            os.remove(Config.SQLITE_DB_PATH)
            print("Removed existing test database to run clean verification.")
        except Exception as e:
            print(f"Notice: Could not remove db file: {e}")
            
    # 2. Initialize Database & Seed Mock Data
    print("\n[Step 1] Initializing database and tables...")
    try:
        DBHelper.init_db()
        print("SUCCESS: Tables created and mock data seeded successfully.")
    except Exception as e:
        print(f"FAILED: Schema creation or seeding failed: {e}")
        return False
        
    # Check tables contents
    users = DBHelper.query_all("SELECT id, fullname, role, email FROM users")
    suppliers = DBHelper.query_all("SELECT id, supplier_name FROM suppliers")
    medicines = DBHelper.query_all("SELECT id, medicine_name, quantity FROM medicines")
    inventory = DBHelper.query_all("SELECT COUNT(*) as count FROM inventory")[0]['count']
    
    print(f"Users found: {len(users)} ({', '.join([u['role'] for u in users])})")
    print(f"Suppliers found: {len(suppliers)}")
    print(f"Medicines found: {len(medicines)}")
    print(f"Inventory Logs found: {inventory}")
    
    if len(users) < 2 or len(suppliers) < 3 or len(medicines) < 6 or inventory < 20:
        print("FAILED: Seed data counts do not match expected profiles.")
        return False
        
    # 3. Test AI predictions models
    print("\n[Step 2] Testing AI ML Demand Classification...")
    try:
        predictions = PredictionHelper.predict_demand()
        print("SUCCESS: ML demand predictions complete.")
        for med_id, pred in predictions.items():
            med_name = next(m['medicine_name'] for m in medicines if m['id'] == med_id)
            print(f" - {med_name}: {pred}")
    except Exception as e:
        print(f"FAILED: ML demand prediction failed: {e}")
        return False
        
    # 4. Test Stock Forecasting (Linear Regression & Plot Generation)
    print("\n[Step 3] Testing Stock Forecasting Linear Regression...")
    try:
        # Test forecast for Paracetamol (medicine id = 1)
        med_id = medicines[0]['id']
        med_name = medicines[0]['medicine_name']
        forecast = PredictionHelper.forecast_stock(med_id)
        print(f"SUCCESS: Forecast computed for {med_name}.")
        print(f" - Days Remaining: {forecast['days_remaining']}")
        print(f" - Status: {forecast['status']}")
        print(f" - Daily Depletion Rate: {abs(forecast['slope'])} units/day")
        print(f" - Forecast Plot URL: {forecast['plot_url']}")
        
        # Verify forecast chart image was actually saved
        if forecast['plot_url']:
            image_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), 
                forecast['plot_url'].lstrip('/')
            )
            if os.path.exists(image_path):
                print(f"SUCCESS: Forecast plot image verified on disk: {image_path}")
            else:
                print(f"FAILED: Forecast plot URL returned but image file was not found: {image_path}")
                return False
    except Exception as e:
        print(f"FAILED: Stock forecasting failed: {e}")
        return False
        
    # 5. Check Restock Suggestions
    print("\n[Step 4] Checking Automated AI Restock Suggestions...")
    try:
        suggestions = PredictionHelper.get_restock_suggestions()
        print(f"SUCCESS: Generated {len(suggestions)} restock recommendation(s).")
        for sug in suggestions:
            print(f" - Suggestion: Order +{sug['suggested_quantity']} of {sug['medicine_name']} from {sug['supplier_name']} ({sug['reason']})")
    except Exception as e:
        print(f"FAILED: Restock suggestions failed: {e}")
        return False
        
    print("\n================ VERIFICATION COMPLETED SUCCESSFULLY ================")
    return True

if __name__ == '__main__':
    success = verify_system()
    sys.exit(0 if success else 1)
