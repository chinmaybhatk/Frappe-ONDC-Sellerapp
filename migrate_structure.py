# Migration script to fix module structure
# This file documents the structure changes needed

# The correct structure for a Frappe app is:
# app_name/
#   ├── module_name/
#   │   ├── __init__.py
#   │   ├── config/
#   │   │   ├── __init__.py
#   │   │   └── desktop.py
#   │   ├── doctype/
#   │   │   ├── __init__.py
#   │   │   └── doctype_name/
#   │   │       ├── __init__.py
#   │   │       ├── doctype_name.py
#   │   │       ├── doctype_name.js
#   │   │       └── doctype_name.json
#   │   └── report/
#   │       ├── __init__.py
#   │       └── report_name/
#   │           ├── __init__.py
#   │           ├── report_name.py
#   │           └── report_name.json
#   ├── api/
#   ├── utils/
#   ├── public/
#   ├── fixtures/
#   ├── hooks.py
#   ├── modules.txt
#   └── __init__.py

# Files need to be moved from:
# ondc_seller_app/modules/ondc_seller/* -> ondc_seller_app/ondc_seller/*

print("Module structure has been documented. Manual migration required.")