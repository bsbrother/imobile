import sys
import os
import asyncio

sys.path.insert(0, '/home/kasm-user/apps/imobile')
from trading.sync_app_to_db import pre_requirements, get_transactions_from_app_history_page_structured, parse_csv_data, parse_number
from shared.db.db import DB

async def main():
    tools, llm, config = await pre_requirements()
    print("Scraping transactions from app...")
    tx_csv = await get_transactions_from_app_history_page_structured(config, llm, tools)
    
    header, app_rows = parse_csv_data(tx_csv)
    print(f"Scraped {len(app_rows)} transactions from app.")
    
    # Format app transactions as keys
    app_keys = set()
    for row in app_rows:
        tx_date = row[0].strip()
        name = row[1].strip()
        tx_type = row[2].strip()
        price = parse_number(row[3])
        quantity = int(parse_number(row[4]))
        amount = parse_number(row[5])
        
        if tx_type in ['证券买入', '买入', 'buy']:
            norm_type = 'buy'
        elif tx_type in ['证券卖出', '卖出', 'sell']:
            norm_type = 'sell'
        else:
            norm_type = tx_type.lower()
            
        key = (name, norm_type, tx_date, price, quantity, amount)
        app_keys.add(key)
        
    # Query database
    with DB.cursor() as cursor:
        db_rows = cursor.execute("""
            SELECT id, name, transaction_type, transaction_date, price, quantity, amount 
            FROM transactions 
            WHERE transaction_date >= '2026-01-01 00:00:00'
        """).fetchall()
        
    print(f"Database has {len(db_rows)} transactions for 2026.")
    
    db_keys = {}
    for r in db_rows:
        key = (r['name'].strip(), r['transaction_type'].strip(), r['transaction_date'].strip(), r['price'], r['quantity'], r['amount'])
        db_keys[key] = r
        
    # Discrepancy 1: In DB but not in App
    missing_in_app = []
    for k, db_item in db_keys.items():
        if k not in app_keys:
            # Let's also check if code-first or cleaned-name mapping can match
            # But here we do direct key match first
            missing_in_app.append(db_item)
            
    # Discrepancy 2: In App but not in DB
    missing_in_db = []
    for k in app_keys:
        if k not in db_keys:
            missing_in_db.append(k)
            
    print("\n=== Present in DB but missing in App sync (Total: {}) ===".format(len(missing_in_app)))
    for item in missing_in_app:
        print(f"ID={item['id']}, Name={item['name']}, Type={item['transaction_type']}, Date={item['transaction_date']}, Price={item['price']}, Qty={item['quantity']}, Amt={item['amount']}")
        
    print("\n=== Present in App but missing in DB (Total: {}) ===".format(len(missing_in_db)))
    for item in missing_in_db:
        print(f"Name={item[0]}, Type={item[1]}, Date={item[2]}, Price={item[3]}, Qty={item[4]}, Amt={item[5]}")

if __name__ == '__main__':
    asyncio.run(main())
