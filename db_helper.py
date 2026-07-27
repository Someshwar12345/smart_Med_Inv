import sqlite3
import datetime
import os
from config import Config
from flask_bcrypt import Bcrypt

bcrypt = Bcrypt()

# Ensure database directory exists for SQLite
if not os.path.exists(os.path.dirname(Config.SQLITE_DB_PATH)):
    os.makedirs(os.path.dirname(Config.SQLITE_DB_PATH))

class DBHelper:
    @staticmethod
    def get_connection():
        """
        Creates and returns a connection to MySQL, or SQLite if MySQL connection fails or DB_TYPE is 'sqlite'
        """
        if Config.DB_TYPE == 'mysql':
            try:
                import mysql.connector
                conn = mysql.connector.connect(
                    host=Config.DB_HOST,
                    user=Config.DB_USER,
                    password=Config.DB_PASSWORD,
                    database=Config.DB_NAME
                )
                return conn
            except Exception as e:
                print(f"MySQL connection failed: {e}. Falling back to SQLite...")
                # Temporarily change DB_TYPE to sqlite for future calls or just proceed
        
        # SQLite Connection
        conn = sqlite3.connect(Config.SQLITE_DB_PATH)
        # Enable foreign keys in SQLite
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    @staticmethod
    def execute(sql, params=()):
        """
        Executes an INSERT, UPDATE, or DELETE query and returns the lastrowid.
        """
        conn = DBHelper.get_connection()
        cursor = conn.cursor()
        
        # Adjust SQL placeholders if using SQLite
        if isinstance(conn, sqlite3.Connection):
            sql = sql.replace('%s', '?')
            
        try:
            cursor.execute(sql, params)
            conn.commit()
            last_id = cursor.lastrowid
            return last_id
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def query_all(sql, params=()):
        """
        Executes a SELECT query and returns all matching records as a list of dictionaries.
        """
        conn = DBHelper.get_connection()
        
        # Adjust SQL placeholders if using SQLite
        is_sqlite = isinstance(conn, sqlite3.Connection)
        if is_sqlite:
            sql = sql.replace('%s', '?')
            
        cursor = conn.cursor()
        try:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            
            # Convert row tuples to dictionaries
            columns = [col[0] for col in cursor.description]
            result = [dict(zip(columns, row)) for row in rows]
            return result
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def query_one(sql, params=()):
        """
        Executes a SELECT query and returns the first matching record as a dictionary.
        """
        conn = DBHelper.get_connection()
        is_sqlite = isinstance(conn, sqlite3.Connection)
        if is_sqlite:
            sql = sql.replace('%s', '?')
            
        cursor = conn.cursor()
        try:
            cursor.execute(sql, params)
            row = cursor.fetchone()
            if not row:
                return None
            columns = [col[0] for col in cursor.description]
            return dict(zip(columns, row))
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def log_audit(user_id, action):
        """
        Logs a user action in the audit logs table.
        """
        sql = "INSERT INTO audit_logs (user_id, action) VALUES (%s, %s)"
        DBHelper.execute(sql, (user_id, action))

    @staticmethod
    def init_db():
        """
        Initializes the database schema and seeds it with mock data if empty.
        """
        conn = DBHelper.get_connection()
        is_sqlite = isinstance(conn, sqlite3.Connection)
        cursor = conn.cursor()
        
        try:
            if is_sqlite:
                # Create SQLite tables
                cursor.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fullname TEXT NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    mobile TEXT UNIQUE NOT NULL,
                    role TEXT NOT NULL,
                    password TEXT NOT NULL,
                    is_verified INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE TABLE IF NOT EXISTS suppliers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    supplier_name TEXT NOT NULL,
                    contact_person TEXT,
                    phone TEXT NOT NULL,
                    email TEXT,
                    address TEXT
                );
                
                CREATE TABLE IF NOT EXISTS medicines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    medicine_name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    batch_no TEXT UNIQUE NOT NULL,
                    manufacturer TEXT,
                    purchase_price REAL NOT NULL,
                    selling_price REAL NOT NULL,
                    quantity INTEGER DEFAULT 0,
                    expiry_date DATE NOT NULL,
                    supplier_id INTEGER,
                    FOREIGN KEY (supplier_id) REFERENCES suppliers(id) ON DELETE SET NULL
                );
                
                CREATE TABLE IF NOT EXISTS inventory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    medicine_id INTEGER NOT NULL,
                    stock_in INTEGER DEFAULT 0,
                    stock_out INTEGER DEFAULT 0,
                    current_stock INTEGER NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (medicine_id) REFERENCES medicines(id) ON DELETE CASCADE
                );
                
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    action TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
                );
                
                CREATE TABLE IF NOT EXISTS otp_verifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    phone_number TEXT NOT NULL,
                    otp_code TEXT NOT NULL,
                    is_verified INTEGER DEFAULT 0,
                    attempts INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                """)
            else:
                # Create MySQL tables using connection cursor (we execute sql lines)
                with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database', 'smart_medical_inventory.sql'), 'r') as f:
                    sql_script = f.read()
                # Split commands and execute
                # NOTE: MySQL connector doesn't always support executescript, so we execute block by block
                for statement in sql_script.split(';'):
                    if statement.strip():
                        cursor.execute(statement)
            
            conn.commit()
        except Exception as e:
            print(f"Error during schema creation: {e}")
        finally:
            cursor.close()
            conn.close()
            
        # Seed Mock Data if users table is empty
        users = DBHelper.query_all("SELECT * FROM users")
        if not users:
            print("Seeding mock database...")
            DBHelper.seed_mock_data()

    @staticmethod
    def seed_mock_data():
        """
        Seeds the database with initial mock users, suppliers, medicines, and 90-day inventory history.
        """
        # Hashed passwords
        admin_pass = bcrypt.generate_password_hash('admin123').decode('utf-8')
        pharm_pass = bcrypt.generate_password_hash('pharm123').decode('utf-8')
        
        # 1. Seed Users (Admin and Pharmacist)
        # Verify status set to 1 (verified)
        user_sql = "INSERT INTO users (fullname, email, mobile, role, password, is_verified) VALUES (%s, %s, %s, %s, %s, 1)"
        admin_id = DBHelper.execute(user_sql, ('System Admin', 'admin@example.com', '+1234567890', 'Admin', admin_pass))
        pharm_id = DBHelper.execute(user_sql, ('John Pharmacist', 'pharmacist@example.com', '+1234567891', 'Pharmacist', pharm_pass))
        
        # 2. Seed Suppliers
        supplier_sql = "INSERT INTO suppliers (supplier_name, contact_person, phone, email, address) VALUES (%s, %s, %s, %s, %s)"
        sup1 = DBHelper.execute(supplier_sql, ('Medix Distributors', 'Robert Carter', '555-0192', 'robert@medix.com', '123 Health Ave, Boston'))
        sup2 = DBHelper.execute(supplier_sql, ('PharmaCare Logistics', 'Emily Vance', '555-0144', 'emily@pharmacare.com', '456 Biotech Parkway, Chicago'))
        sup3 = DBHelper.execute(supplier_sql, ('Apex Medical Supplies', 'David Miller', '555-0188', 'david@apexmed.com', '789 Clinical Dr, Austin'))
        
        # 3. Seed Medicines
        # Need: medicine_name, category, batch_no, manufacturer, purchase_price, selling_price, quantity, expiry_date, supplier_id
        # We will hardcode dates relative to current time: 2026-07-19
        today = datetime.date(2026, 7, 19)
        
        med_sql = """
            INSERT INTO medicines (medicine_name, category, batch_no, manufacturer, purchase_price, selling_price, quantity, expiry_date, supplier_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        
        # Medicines: Active, Low Stock, Expired, and Expiring Soon
        m1 = DBHelper.execute(med_sql, ('Paracetamol 500mg', 'Analgesics', 'PM-0912', 'GlaxoSmithKline', 1.20, 2.50, 120, today + datetime.timedelta(days=500), sup1))
        m2 = DBHelper.execute(med_sql, ('Amoxicillin 250mg', 'Antibiotics', 'AM-2831', 'Sandoz', 3.50, 6.00, 40, today + datetime.timedelta(days=15), sup2)) # Expiring soon
        m3 = DBHelper.execute(med_sql, ('Ibuprofen 400mg', 'Analgesics', 'IB-4821', 'Pfizer', 1.50, 3.00, 5, today - datetime.timedelta(days=10), sup1)) # Expired & Low Stock
        m4 = DBHelper.execute(med_sql, ('Metformin 500mg', 'Antidiabetic', 'MF-8391', 'Merck', 2.00, 4.50, 200, today + datetime.timedelta(days=400), sup3))
        m5 = DBHelper.execute(med_sql, ('Atorvastatin 20mg', 'Cardiovascular', 'AT-1928', 'Lipitor', 4.00, 8.50, 12, today + datetime.timedelta(days=250), sup2)) # Low stock
        m6 = DBHelper.execute(med_sql, ('Vitamin C 1000mg', 'Supplements', 'VC-3829', 'Nature Made', 0.80, 2.00, 300, today + datetime.timedelta(days=600), sup3))
        
        # 4. Seed Inventory History (spanning past 90 days) to allow linear regression predictions
        # Paracetamol (m1): starting stock 200, steadily decreasing
        # Metformin (m4): starting stock 400, decreasing
        # Ibuprofen (m3): starting stock 100, dropping to 5
        # We will write historical updates into the `inventory` table
        
        history = [
            # Paracetamol m1: Initial stock 90 days ago = 200
            (m1, 200, 0, 200, 90),
            (m1, 0, 30, 170, 60),
            (m1, 0, 25, 145, 30),
            (m1, 0, 15, 130, 15),
            (m1, 0, 10, 120, 0), # Current quantity matches medicine quantity (120)
            
            # Metformin m4: Initial stock 90 days ago = 400
            (m4, 400, 0, 400, 90),
            (m4, 0, 80, 320, 60),
            (m4, 0, 70, 250, 30),
            (m4, 0, 35, 215, 15),
            (m4, 0, 15, 200, 0), # Current quantity matches medicine quantity (200)
            
            # Ibuprofen m3: Initial stock 90 days ago = 80
            (m3, 80, 0, 80, 90),
            (m3, 0, 30, 50, 60),
            (m3, 0, 25, 25, 30),
            (m3, 0, 15, 10, 15),
            (m3, 0, 5, 5, 0), # Current quantity matches medicine quantity (5)
            
            # Amoxicillin m2: Initial stock 30 days ago = 100
            (m2, 100, 0, 100, 30),
            (m2, 0, 40, 60, 15),
            (m2, 0, 20, 40, 0), # Current quantity matches medicine quantity (40)
            
            # Atorvastatin m5: Initial stock 60 days ago = 50
            (m5, 50, 0, 50, 60),
            (m5, 0, 20, 30, 30),
            (m5, 0, 10, 20, 15),
            (m5, 0, 8, 12, 0), # Current quantity matches medicine quantity (12)
            
            # Vitamin C m6: Initial stock 90 days ago = 500
            (m6, 500, 0, 500, 90),
            (m6, 0, 100, 400, 60),
            (m6, 0, 60, 340, 30),
            (m6, 0, 40, 300, 0) # Current quantity matches medicine quantity (300)
        ]
        
        inv_sql = """
            INSERT INTO inventory (medicine_id, stock_in, stock_out, current_stock, updated_at)
            VALUES (%s, %s, %s, %s, %s)
        """
        
        for med_id, s_in, s_out, curr, days_ago in history:
            timestamp = today - datetime.timedelta(days=days_ago)
            # SQLite uses ISO format strings for datetime
            time_str = timestamp.strftime('%Y-%m-%d %H:%M:%S')
            DBHelper.execute(inv_sql, (med_id, s_in, s_out, curr, time_str))
            
        # 5. Seed Audit Logs
        DBHelper.log_audit(admin_id, "System initialized and mock data seeded.")
        DBHelper.log_audit(pharm_id, "Pharmacist user activated.")
        
        print("Mock data seeded successfully!")
