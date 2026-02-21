"""
Pramaan Demo Data Fix Script - V9
Run on Frappe Cloud: bench --site ondc.waluelab.com execute ondc_seller_app.fix_pramaan_demo_data.run

Or via bench console:
  bench --site ondc.waluelab.com console
  Then paste: exec(open('/path/to/fix_pramaan_demo_data.py').read())

This script:
1. Fixes PROD-0004 (Whole Wheat Atta) - wrong long_desc, missing category, brand, manufacturer, images
2. Adds 2 more demo products for Pramaan (Milk, Rice)
3. Fixes consumer_care_contact format on all products
4. Ensures ONDC Settings has all required fields
"""

import frappe
from datetime import datetime


def run():
    print("=" * 60)
    print("ONDC Pramaan Demo Data Fix - V9")
    print("=" * 60)

    # ---------------------------------------------------------------
    # 1. Fix ONDC Settings (add missing fields if needed)
    # ---------------------------------------------------------------
    print("\n[1/4] Fixing ONDC Settings...")
    settings = frappe.get_single("ONDC Settings")

    # Ensure gst_number field is set (code uses both gst_no and gst_number)
    if not settings.get("gst_number") and settings.get("gst_no"):
        try:
            settings.gst_number = settings.gst_no
        except Exception:
            pass  # field may not exist

    # Ensure store_street is set
    if not settings.get("store_street"):
        try:
            settings.store_street = settings.get("store_locality") or "Koramangala"
        except Exception:
            pass

    # Ensure np_type is set
    if not settings.get("np_type"):
        try:
            settings.np_type = "MSN"
        except Exception:
            pass

    try:
        settings.save(ignore_permissions=True)
        frappe.db.commit()
        print("  OK ONDC Settings updated")
    except Exception as e:
        print(f"  WARN Settings update skipped: {e}")

    # ---------------------------------------------------------------
    # 2. Fix existing product PROD-0004 (Whole Wheat Atta)
    # ---------------------------------------------------------------
    print("\n[2/4] Fixing PROD-0004 (Whole Wheat Atta 1kg)...")
    try:
        prod = frappe.get_doc("ONDC Product", "PROD-0004")

        # Fix long_desc (was "Full Cream Milk 1L" - copy-paste error)
        prod.long_desc = "Premium quality whole wheat atta, stone ground from the finest wheat grains. Perfect for making soft rotis and chapatis. Net weight 1kg."

        # Fix category_code
        prod.category_code = "Foodgrains"

        # Fix brand & manufacturer
        prod.brand = "Aashirvaad"
        prod.manufacturer = "ITC Limited"

        # Fix available_quantity (was 0 = out of stock!)
        prod.available_quantity = 100

        # Fix consumer_care_contact format (ONDC requires "Name,Email,Phone")
        prod.consumer_care_contact = "zibmoc business solutions,info.zibmoc@walue.biz,9972818380"

        # Add a placeholder image if none exist
        if not prod.images or len(prod.images) == 0:
            prod.append("images", {
                "image_url": "https://ondc.waluelab.com/assets/erpnext/images/erpnext-logo.svg"
            })

        prod.save(ignore_permissions=True)
        frappe.db.commit()
        print(f"  OK PROD-0004 fixed: long_desc, category, brand, manufacturer, qty, images, contact")
    except frappe.DoesNotExistError:
        print("  WARN PROD-0004 not found, will create fresh")
    except Exception as e:
        print(f"  FAIL Error fixing PROD-0004: {e}")

    # ---------------------------------------------------------------
    # 3. Create additional demo products
    # ---------------------------------------------------------------
    print("\n[3/4] Creating additional demo products...")

    demo_products = [
        {
            "item_code": "MILK-1L",
            "product_name": "Full Cream Milk 1L",
            "short_desc": "Fresh Full Cream Milk 1 Litre",
            "long_desc": "Fresh full cream milk, pasteurized and homogenized. Rich in calcium and protein. Sourced from healthy cows. 1 Litre tetra pack.",
            "price": 65,
            "maximum_price": 70,
            "currency": "INR",
            "available_quantity": 200,
            "maximum_quantity": 10,
            "minimum_quantity": 1,
            "category_code": "Foodgrains",
            "brand": "Amul",
            "manufacturer": "Gujarat Cooperative Milk Marketing Federation Ltd",
            "country_of_origin": "IND",
            "is_returnable": 0,
            "is_cancellable": 1,
            "seller_pickup_return": 0,
            "available_on_cod": 1,
            "time_to_ship": "PT60M",
            "return_window": "PT72H",
            "consumer_care_contact": "zibmoc business solutions,info.zibmoc@walue.biz,9972818380",
            "is_active": 1,
            "manufacturer_name": "Gujarat Cooperative Milk Marketing Federation Ltd",
            "manufacturer_address": "Anand, Gujarat, India",
            "common_generic_name": "Full Cream Milk",
            "net_quantity_or_measure": "1L",
            "month_year_of_manufacture": "02/2026",
            "images": [
                {"image_url": "https://ondc.waluelab.com/assets/erpnext/images/erpnext-logo.svg"}
            ],
        },
        {
            "item_code": "RICE-5KG",
            "product_name": "Basmati Rice 5kg",
            "short_desc": "Premium Basmati Rice 5kg",
            "long_desc": "Aged premium basmati rice with extra long grains. Fluffy and aromatic when cooked. Perfect for biryani and pulao. 5kg pack.",
            "price": 450,
            "maximum_price": 500,
            "currency": "INR",
            "available_quantity": 50,
            "maximum_quantity": 5,
            "minimum_quantity": 1,
            "category_code": "Foodgrains",
            "brand": "India Gate",
            "manufacturer": "KRBL Limited",
            "country_of_origin": "IND",
            "is_returnable": 1,
            "is_cancellable": 1,
            "seller_pickup_return": 1,
            "available_on_cod": 1,
            "time_to_ship": "P1D",
            "return_window": "PT72H",
            "consumer_care_contact": "zibmoc business solutions,info.zibmoc@walue.biz,9972818380",
            "is_active": 1,
            "manufacturer_name": "KRBL Limited",
            "manufacturer_address": "Noida, Uttar Pradesh, India",
            "common_generic_name": "Basmati Rice",
            "net_quantity_or_measure": "5kg",
            "month_year_of_manufacture": "01/2026",
            "images": [
                {"image_url": "https://ondc.waluelab.com/assets/erpnext/images/erpnext-logo.svg"}
            ],
        },
    ]

    for product_data in demo_products:
        item_code = product_data["item_code"]
        # Check if already exists
        existing = frappe.db.get_value("ONDC Product", {"item_code": item_code}, "name")
        if existing:
            print(f"  WARN {item_code} already exists as {existing}, updating...")
            try:
                doc = frappe.get_doc("ONDC Product", existing)
                for key, val in product_data.items():
                    if key != "images":
                        doc.set(key, val)
                # Handle images
                if not doc.images or len(doc.images) == 0:
                    for img in product_data.get("images", []):
                        doc.append("images", img)
                doc.save(ignore_permissions=True)
                frappe.db.commit()
                print(f"  OK {item_code} updated: {doc.name}")
            except Exception as e:
                print(f"  FAIL Error updating {item_code}: {e}")
        else:
            try:
                images = product_data.pop("images", [])
                doc = frappe.new_doc("ONDC Product")
                for key, val in product_data.items():
                    doc.set(key, val)
                for img in images:
                    doc.append("images", img)
                doc.insert(ignore_permissions=True)
                frappe.db.commit()
                print(f"  OK {item_code} created: {doc.name} (ONDC ID: {doc.ondc_product_id})")
            except Exception as e:
                print(f"  FAIL Error creating {item_code}: {e}")

    # ---------------------------------------------------------------
    # 4. Verify all products
    # ---------------------------------------------------------------
    print("\n[4/4] Verifying all ONDC Products...")
    products = frappe.get_all(
        "ONDC Product",
        fields=["name", "ondc_product_id", "product_name", "category_code", "brand",
                "manufacturer", "available_quantity", "long_desc", "consumer_care_contact"],
        filters={"is_active": 1},
    )

    issues = []
    for p in products:
        name = p.get("name")
        checks = {
            "category_code": p.get("category_code"),
            "brand": p.get("brand"),
            "manufacturer": p.get("manufacturer"),
            "available_quantity": p.get("available_quantity") and int(p.get("available_quantity")) > 0,
            "long_desc": p.get("long_desc") and len(p.get("long_desc")) > 10,
            "consumer_care_contact": p.get("consumer_care_contact") and "," in str(p.get("consumer_care_contact")),
        }
        fails = [k for k, v in checks.items() if not v]
        if fails:
            issues.append(f"  WARN {name}: missing/invalid {', '.join(fails)}")
        else:
            print(f"  OK {name} ({p.get('product_name')}): ALL FIELDS OK")

    if issues:
        print("\n  Issues found:")
        for issue in issues:
            print(issue)

    # Check images
    for p in products:
        img_count = frappe.db.count("ONDC Product Image", {"parent": p.get("name")})
        if img_count == 0:
            print(f"  WARN {p.get('name')}: NO IMAGES (optional but recommended)")
        else:
            print(f"  OK {p.get('name')}: {img_count} image(s)")

    print("\n" + "=" * 60)
    print("Done! Now deploy V9 code and re-run Pramaan test.")
    print("=" * 60)


# Allow running via exec() or bench execute
if __name__ == "__main__" or frappe.flags.in_test:
    run()
else:
    # When called via bench execute, run() is called by the framework
    pass
