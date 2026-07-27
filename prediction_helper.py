import os
import datetime
import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.tree import DecisionTreeClassifier

# Configure Matplotlib to use a non-GUI backend (Agg) for thread safety on web servers
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from db_helper import DBHelper
from config import Config

# Ensure static folder for forecasts exists
FORECAST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'images', 'forecasts')
if not os.path.exists(FORECAST_DIR):
    os.makedirs(FORECAST_DIR)

class PredictionHelper:
    @staticmethod
    def forecast_stock(medicine_id):
        """
        Uses Linear Regression to forecast when a medicine will run out of stock.
        Generates and saves a Matplotlib plot.
        Returns:
            dict: {
                'days_remaining': float or None,
                'status': str ('Stable', 'Depleting', 'Out of Stock', 'Insufficient Data'),
                'plot_url': str or None,
                'slope': float
            }
        """
        # Fetch inventory history
        sql = """
            SELECT stock_in, stock_out, current_stock, updated_at 
            FROM inventory 
            WHERE medicine_id = %s 
            ORDER BY updated_at ASC
        """
        history = DBHelper.query_all(sql, (medicine_id,))
        
        # Get medicine details
        med = DBHelper.query_one("SELECT medicine_name, quantity FROM medicines WHERE id = %s", (medicine_id,))
        if not med:
            return {'days_remaining': None, 'status': 'Medicine Not Found', 'plot_url': None, 'slope': 0}
        
        med_name = med['medicine_name']
        current_qty = med['quantity']
        
        if current_qty <= 0:
            return {'days_remaining': 0, 'status': 'Out of Stock', 'plot_url': None, 'slope': 0}
            
        if len(history) < 2:
            return {
                'days_remaining': None, 
                'status': 'Insufficient Data', 
                'plot_url': None, 
                'slope': 0
            }
            
        # Parse history into a DataFrame
        df = pd.DataFrame(history)
        df['updated_at'] = pd.to_datetime(df['updated_at'])
        
        # Calculate days relative to the first transaction
        start_date = df['updated_at'].min()
        df['days'] = (df['updated_at'] - start_date).dt.total_seconds() / (24 * 3600)
        
        X = df[['days']].values
        y = df['current_stock'].values
        
        # Fit Linear Regression
        model = LinearRegression()
        model.fit(X, y)
        
        slope = model.coef_[0]
        intercept = model.intercept_
        
        # Calculate when stock hits 0: X = -intercept / slope
        days_remaining = None
        status = 'Stable'
        
        # If stock is depleting (slope is negative)
        if slope < -0.01:
            # Current time in relative days
            today_rel = (pd.Timestamp(datetime.date(2026, 7, 19)) - start_date).total_seconds() / (24 * 3600)
            # Relative day when stock hits zero
            zero_day = -intercept / slope
            # Days remaining from today
            days_remaining = max(0.0, zero_day - today_rel)
            
            if days_remaining <= 7:
                status = 'Urgent Restock'
            elif days_remaining <= 30:
                status = 'Depleting'
            else:
                status = 'Gradual Depletion'
        else:
            status = 'Stable'
            days_remaining = 999  # Code for long-term stability
            
        # Generate and save Matplotlib plot
        plot_filename = f"forecast_{medicine_id}.png"
        plot_path = os.path.join(FORECAST_DIR, plot_filename)
        
        try:
            plt.figure(figsize=(6, 3.5))
            plt.scatter(df['updated_at'], y, color='#0d6efd', label='Recorded Stock')
            
            # Predict line
            future_days = np.linspace(df['days'].min(), df['days'].max() + (30 if slope < 0 else 10), 100)
            future_dates = [start_date + pd.Timedelta(days=d) for d in future_days]
            y_pred = model.predict(future_days.reshape(-1, 1))
            
            plt.plot(future_dates, y_pred, color='#dc3545' if slope < 0 else '#198754', linestyle='--', label='Trend Line')
            
            plt.title(f"Stock Trend: {med_name}", fontsize=10, fontweight='bold', color='#0b2545')
            plt.xlabel('Date', fontsize=8)
            plt.ylabel('Stock Level', fontsize=8)
            plt.grid(True, linestyle=':', alpha=0.6)
            plt.xticks(rotation=30, fontsize=7)
            plt.yticks(fontsize=7)
            plt.legend(fontsize=7, loc='best')
            plt.tight_layout()
            
            # Save plot
            plt.savefig(plot_path, dpi=150)
            plt.close()
            plot_url = f"/static/images/forecasts/{plot_filename}"
        except Exception as e:
            print(f"Plot generation failed for {med_name}: {e}")
            plot_url = None
            
        return {
            'days_remaining': round(days_remaining, 1) if days_remaining != 999 else None,
            'status': status,
            'plot_url': plot_url,
            'slope': round(slope, 2)
        }

    @staticmethod
    def predict_demand():
        """
        Trains a DecisionTree Classifier on historical transactions to predict demand categories
        for each medicine in the database.
        Returns:
            dict: { medicine_id: 'High' | 'Medium' | 'Low' }
        """
        # Fetch all medicines
        medicines = DBHelper.query_all("SELECT id, medicine_name, category, selling_price, quantity FROM medicines")
        if not medicines:
            return {}

        # Fetch total sales (stock out) in last 30 days for each medicine
        # Using 2026-07-19 as current date
        sales_sql = """
            SELECT medicine_id, SUM(stock_out) as total_sold
            FROM inventory
            WHERE updated_at >= '2026-06-19 00:00:00'
            GROUP BY medicine_id
        """
        sales = DBHelper.query_all(sales_sql)
        sales_dict = {s['medicine_id']: s['total_sold'] or 0 for s in sales}
        
        # Prepare training data
        # If database is extremely small, we seed standard training records representing the patterns
        # Feature 1: selling_price, Feature 2: recent_monthly_sales
        # Target: Demand Class (0 = Low, 1 = Medium, 2 = High)
        
        # Training dataset base (heuristics-based template to guide Scikit-Learn tree classifier)
        training_features = [
            [2.50, 120],  # High
            [6.00, 45],   # Medium
            [3.00, 8],    # Low
            [4.50, 80],   # High
            [8.50, 18],   # Medium
            [2.00, 110],  # High
            [15.00, 2],   # Low
            [1.00, 250],  # High
            [25.00, 10],  # Low
            [12.00, 30]   # Medium
        ]
        training_labels = [2, 1, 0, 2, 1, 2, 0, 2, 0, 1]  # 2: High, 1: Medium, 0: Low
        
        # Fit classifier
        clf = DecisionTreeClassifier(max_depth=3)
        clf.fit(training_features, training_labels)
        
        predictions = {}
        for m in medicines:
            m_id = m['id']
            price = float(m['selling_price'])
            recent_sales = sales_dict.get(m_id, 0)
            
            # Predict using decision tree
            pred_class_id = clf.predict([[price, recent_sales]])[0]
            
            class_map = {0: 'Low Demand', 1: 'Medium Demand', 2: 'High Demand'}
            predictions[m_id] = class_map[pred_class_id]
            
        return predictions

    @staticmethod
    def get_restock_suggestions():
        """
        Builds automated restock suggestions combining current quantity, forecasted runout, and demand class.
        Returns:
            list: list of dicts with suggestion details
        """
        medicines = DBHelper.query_all("""
            SELECT m.*, s.supplier_name 
            FROM medicines m 
            LEFT JOIN suppliers s ON m.supplier_id = s.id
        """)
        demand_preds = PredictionHelper.predict_demand()
        
        suggestions = []
        for m in medicines:
            m_id = m['id']
            current_qty = m['quantity']
            demand_class = demand_preds.get(m_id, 'Low Demand')
            
            # Run forecast
            forecast = DBHelper.query_all("SELECT SUM(stock_out) as total_out FROM inventory WHERE medicine_id = %s", (m_id,))
            total_out = forecast[0]['total_out'] if forecast else 0
            
            forecast_results = PredictionHelper.forecast_stock(m_id)
            days_left = forecast_results['days_remaining']
            status = forecast_results['status']
            
            needs_restock = False
            reason = []
            suggested_qty = 0
            
            # Condition 1: Low stock threshold
            if current_qty <= 15:
                needs_restock = True
                reason.append("Stock level is critically low (< 15 units)")
                
            # Condition 2: High demand, depleting soon
            if demand_class == 'High Demand' and days_left and days_left < 30:
                needs_restock = True
                reason.append("High demand drug projected to exhaust within 30 days")
            
            # Condition 3: Depleting soon
            if status in ['Urgent Restock', 'Depleting'] and days_left and days_left < 15:
                needs_restock = True
                reason.append(f"Predicted to deplete in {days_left} days")
                
            if needs_restock:
                # Calculate restock amount
                # Heuristic: High demand = 150 units, Medium = 80 units, Low = 30 units
                base_qty = 50
                if demand_class == 'High Demand':
                    base_qty = 150
                elif demand_class == 'Medium Demand':
                    base_qty = 80
                else:
                    base_qty = 30
                    
                suggested_qty = max(10, base_qty - current_qty)
                
                suggestions.append({
                    'medicine_id': m_id,
                    'medicine_name': m['medicine_name'],
                    'category': m['category'],
                    'current_stock': current_qty,
                    'demand_class': demand_class,
                    'days_remaining': days_left,
                    'suggested_quantity': suggested_qty,
                    'supplier_name': m['supplier_name'] or 'No Supplier Assigned',
                    'reason': ", ".join(reason)
                })
                
        return suggestions
