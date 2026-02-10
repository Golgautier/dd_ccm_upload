#!/usr/bin/env python3
"""
CSV Converter Program
Lists CSV files in cost_file directory, asks user to select one,
and converts it to a new CSV file with mapped fields.
"""

import os
import json
import csv
import argparse
import shutil
from datetime import datetime
from pathlib import Path
import urllib.request
import urllib.error

# Try to import requests for direct CSV upload
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

# Datadog API client imports
try:
    from datadog_api_client import ApiClient, Configuration
    from datadog_api_client.v2.api import cloud_cost_management_api
    from datadog_api_client.v2.model.custom_costs_file_line_item import CustomCostsFileLineItem
    DATADOG_API_AVAILABLE = True
except ImportError:
    DATADOG_API_AVAILABLE = False


def load_datadog_config(config_path=".datadog.json"):
    """Load Datadog API configuration from JSON file.
    
    The config file contains:
    - api_key: Datadog API key
    - app_key: Datadog Application key
    - site: Datadog site (e.g., "datadoghq.eu", "datadoghq.com")
    
    Args:
        config_path: Path to the Datadog JSON configuration file
        
    Returns:
        Dictionary containing Datadog configuration, or None if file not found or invalid
    """
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Warning: Datadog configuration file '{config_path}' not found.")
        return None
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in Datadog configuration file: {e}")
        return None

def load_config(config_path=".config.json"):
    """Load configuration from JSON file.
    
    The config file contains:
    - SourceFilesDirectory: Directory where source CSV files are located (default: "cost_file")
    - UploadFilePrefix: Prefix for output CSV files
    - DatadogCostFilesLimit: Maximum number of Datadog files to fetch
    - DeduplicationComparisonFields: Fields used for deduplication
    - currency: Dictionary mapping currency codes to exchange rates or "dynamic"
      - If value is a number: use that fixed exchange rate to USD
      - If value is "dynamic": fetch exchange rate from internet
      - If currency not in config: fetch from internet (default behavior)
    - MandatoryFieldsTargetSourceMappingß: Maps target field names to source CSV column names
    - TagsFieldsTargetSourceMapping: Maps tag field names to source CSV column names
    
    Args:
        config_path: Path to the JSON configuration file
        
    Returns:
        Dictionary containing configuration, or None if file not found or invalid
    """
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Error: Configuration file '{config_path}' not found.")
        return None
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in configuration file: {e}")
        return None

def list_csv_files(directory):
    """List all CSV files in the specified directory.
    
    Scans the directory for files with .csv extension and returns
    a sorted list of filenames.
    
    Args:
        directory: Directory path to scan for CSV files
        
    Returns:
        Sorted list of CSV filenames, empty list if directory doesn't exist
    """
    csv_files = []
    dir_path = Path(directory)
    
    if not dir_path.exists():
        print(f"Error: Directory '{directory}' does not exist.")
        return csv_files
    
    # Find all CSV files in the directory
    for file in dir_path.glob("*.csv"):
        csv_files.append(file.name)
    
    # Return sorted list for consistent display order
    return sorted(csv_files)

def select_csv_file(csv_files, auto_yes=False, directory_name="cost_file"):
    """Ask user to select a CSV file from the list via keyboard input.
    
    Displays a numbered list of available CSV files and prompts the user
    to enter a number to select which file to process.
    
    Args:
        csv_files: List of CSV filenames to choose from
        auto_yes: If True, automatically select the first file without prompting
        directory_name: Name of the directory (for display purposes)
        
    Returns:
        Selected filename string, or None if cancelled or no files available
    """
    if not csv_files:
        print(f"No CSV files found in the {directory_name} directory.")
        return None
    
    # Display numbered list of available files
    print("\nAvailable CSV files:")
    for idx, filename in enumerate(csv_files, start=1):
        print(f"  {idx}. {filename}")
    
    # If auto_yes is enabled, automatically select the first file
    if auto_yes:
        selected = csv_files[0]
        print(f"\nAuto-selected (first file): {selected}")
        return selected
    
    # Prompt user for selection with input validation
    while True:
        try:
            choice = input(f"\nSelect a file (1-{len(csv_files)}): ").strip()
            file_index = int(choice) - 1
            
            # Validate index is within range
            if 0 <= file_index < len(csv_files):
                return csv_files[file_index]
            else:
                print(f"Please enter a number between 1 and {len(csv_files)}.")
        except ValueError:
            print("Please enter a valid number.")
        except KeyboardInterrupt:
            print("\nOperation cancelled.")
            return None

def get_exchange_rate(from_currency, to_currency="USD"):
    """Get exchange rate from internet using exchangerate-api.com.
    
    Fetches real-time exchange rates from a free public API.
    The rate represents how many units of 'to_currency' equal 1 unit of 'from_currency'.
    
    Args:
        from_currency: Source currency code (e.g., "EUR", "GBP")
        to_currency: Target currency code (default: "USD")
        
    Returns:
        Exchange rate as float, or None if fetch failed
    """
    try:
        # Use exchangerate-api.com free API (no key required for basic usage)
        # API endpoint returns latest exchange rates for the base currency
        url = f"https://api.exchangerate-api.com/v4/latest/{from_currency}"
        
        with urllib.request.urlopen(url, timeout=10) as response:
            # Parse JSON response containing exchange rates
            data = json.loads(response.read().decode())
            rates = data.get("rates", {})
            
            # Extract the rate for the target currency
            if to_currency in rates:
                return rates[to_currency]
            else:
                print(f"Warning: Could not find exchange rate for {to_currency}")
                return None
                
    except urllib.error.URLError as e:
        # Network error (no internet, API down, etc.)
        print(f"Error fetching exchange rate: {e}")
        return None
    except json.JSONDecodeError as e:
        # Invalid JSON response
        print(f"Error parsing exchange rate response: {e}")
        return None
    except Exception as e:
        # Catch any other unexpected errors
        print(f"Unexpected error fetching exchange rate: {e}")
        return None

def detect_and_report_currencies(input_file_path, billing_currency_field, config=None):
    """Detect currencies in CSV file and report conversion rates if not USD.
    
    Scans the CSV file to find all unique currency values in the billing currency column.
    If non-USD currencies are found, determines exchange rates either from configuration
    or by fetching from the internet.
    
    Args:
        input_file_path: Path to the input CSV file
        billing_currency_field: Name of the column containing currency codes
        config: Configuration dictionary (optional) containing currency rates
        
    Returns:
        Dictionary mapping currency code to exchange rate (e.g., {"EUR": 1.085000}),
        or None if all currencies are USD or an error occurred
    """
    if config is None:
        config = {}
    
    # Get currency configuration
    currency_config = config.get("currency", {})
    
    detected_currencies = set()
    exchange_rates = {}
    
    try:
        with open(input_file_path, 'r', encoding='utf-8') as infile:
            # Auto-detect CSV delimiter (comma, semicolon, tab, etc.)
            sample = infile.read(1024)
            infile.seek(0)  # Reset file pointer to beginning
            sniffer = csv.Sniffer()
            delimiter = sniffer.sniff(sample).delimiter
            
            reader = csv.DictReader(infile, delimiter=delimiter)
            
            # Collect all unique currency codes from the CSV
            # Using a set to automatically handle duplicates
            for row in reader:
                currency = row.get(billing_currency_field, "").strip().upper()
                if currency:
                    detected_currencies.add(currency)
            
            # Filter out USD currencies - we only need to convert non-USD ones
            non_usd_currencies = [curr for curr in detected_currencies if curr != "USD"]
            
            if non_usd_currencies:
                # Display warning and determine exchange rates for each non-USD currency
                print(f"\n⚠️  Non-USD currency detected: {', '.join(non_usd_currencies)}")
                print("\nExchange rates to USD:")
                print("-" * 50)
                
                # Determine rates for each currency
                for currency in sorted(non_usd_currencies):
                    rate = None
                    source = None
                    
                    # Check if currency is configured
                    if currency in currency_config:
                        currency_value = currency_config[currency]
                        
                        if currency_value == "dynamic":
                            # Fetch rate from internet
                            rate = get_exchange_rate(currency, "USD")
                            source = "internet (dynamic)"
                        elif isinstance(currency_value, (int, float)) and currency_value > 0:
                            # Use configured rate
                            rate = float(currency_value)
                            source = "configuration"
                        else:
                            print(f"  {currency} → USD: Invalid configuration value (must be a number or 'dynamic')")
                            continue
                    else:
                        # No configuration, fetch from internet (default behavior)
                        rate = get_exchange_rate(currency, "USD")
                        source = "internet (default)"
                    
                    if rate:
                        exchange_rates[currency] = rate
                        print(f"  {currency} → USD: {rate:.6f} (1 {currency} = {rate:.6f} USD) [{source}]")
                    else:
                        print(f"  {currency} → USD: Unable to fetch rate")
                
                print("-" * 50)
                return exchange_rates
            else:
                # All currencies are already USD, no conversion needed
                print("\n✓ All currencies are USD")
                return None
                
    except Exception as e:
        print(f"Error detecting currencies: {e}")
        return None

def format_iso_to_yyyy_mm_dd(date_value):
    """Format a ISO timestamp to YYYY-MM-DD format.
    
    Args:
        date_value: ISO timestamp string
        
    Returns:
        Date string in YYYY-MM-DD format, or original value if parsing fails
    """
    if not date_value:
        return date_value
    
    return datetime.fromisoformat(date_value).strftime("%Y-%m-%d")

def format_epochmillis_to_yyyy_mm_dd(date_value):
    """Format a epochmillis timestamp to YYYY-MM-DD format.
    
    Args:
        date_value: Timestamp string
        
    Returns:
        Date string in YYYY-MM-DD format, or original value if parsing fails
    """
    return datetime.fromtimestamp(float(date_value)/1000).strftime("%Y-%m-%d")

def get_conversion_rates_from_user(exchange_rates, auto_yes=False):
    """Ask user to confirm or enter manual conversion rates for each currency.
    
    For each non-USD currency detected, prompts the user to either:
    - Accept the automatically fetched exchange rate (y/yes/Enter)
    - Enter a manual conversion rate (n/no, then enter rate)
    - Enter a rate directly as a number
    
    Args:
        exchange_rates: Dictionary mapping currency codes to automatically fetched rates
        auto_yes: If True, automatically accept all automatic rates without prompting
        
    Returns:
        Dictionary mapping currency codes to final conversion rates to use,
        or None if user cancels the operation
    """
    conversion_rates = {}
    
    # Process each currency that needs conversion
    for currency, auto_rate in exchange_rates.items():
        print(f"\nCurrency: {currency}")
        print(f"  Automatic rate: {auto_rate:.6f}")
        
        # If auto_yes is enabled, automatically use the automatic rate
        if auto_yes:
            conversion_rates[currency] = auto_rate
            print(f"  ✓ Using automatic rate: {auto_rate:.6f}")
            continue
        
        # Keep asking until valid input is provided
        while True:
            try:
                choice = input(f"  Use automatic rate? (y/n) or enter manual rate: ").strip().lower()
                
                # User accepts automatic rate (default: Enter or 'y')
                if choice == 'y' or choice == 'yes' or choice == '':
                    conversion_rates[currency] = auto_rate
                    print(f"  ✓ Using automatic rate: {auto_rate:.6f}")
                    break
                # User wants to enter manual rate
                elif choice == 'n' or choice == 'no':
                    manual_rate = input(f"  Enter manual conversion rate for {currency} → USD: ").strip()
                    try:
                        rate_value = float(manual_rate)
                        if rate_value > 0:
                            conversion_rates[currency] = rate_value
                            print(f"  ✓ Using manual rate: {rate_value:.6f}")
                            break
                        else:
                            print("  Error: Rate must be greater than 0.")
                    except ValueError:
                        print("  Error: Please enter a valid number.")
                else:
                    # User entered a number directly (shortcut: just type the rate)
                    try:
                        rate_value = float(choice)
                        if rate_value > 0:
                            conversion_rates[currency] = rate_value
                            print(f"  ✓ Using manual rate: {rate_value:.6f}")
                            break
                        else:
                            print("  Error: Rate must be greater than 0.")
                    except ValueError:
                        print("  Error: Please enter 'y', 'n', or a valid number.")
            except KeyboardInterrupt:
                # User cancelled with Ctrl+C
                print("\nOperation cancelled.")
                return None
    
    return conversion_rates

def convert_csv(input_file_path, output_file_path, config, auto_yes=False):
    """Convert CSV file to JSON format using field mappings from config.
    
    Main conversion function that:
    1. Detects currencies and fetches exchange rates if needed
    2. Prompts user for conversion rate confirmation
    3. Maps source CSV columns to target columns based on config
    4. Converts costs to USD if non-USD currencies are present
    5. Writes the converted data as JSON to output file
    
    Args:
        input_file_path: Path to the source CSV file
        output_file_path: Path where the converted JSON will be written (should have .json extension)
        config: Configuration dictionary with field mappings
        auto_yes: If True, automatically accept all prompts without user input
        
    Returns:
        True if conversion successful, False otherwise
    """
    # Extract field mappings from configuration
    # MandatoryFieldsTargetSourceMappingß maps target field names to source CSV column names
    mandatory_mapping = config.get("MandatoryFieldsTargetSourceMappingß", {})
    
    # TagsFieldsTargetSourceMapping maps tag field names to source CSV column names
    # These will be added as additional columns in the output
    tags_mapping = config.get("TagsFieldsTargetSourceMapping", {})
    
    # Get the source column names for currency and cost fields
    # These are needed for currency detection and cost conversion
    billing_currency_source = mandatory_mapping.get("BillingCurrency", "BillingCurrency")
    billed_cost_source = mandatory_mapping.get("BilledCost", "BilledCost")
    
    # Step 1: Detect currencies in the CSV and fetch exchange rates if needed
    exchange_rates = detect_and_report_currencies(input_file_path, billing_currency_source, config)
    
    # Step 2: Get conversion rates from user if non-USD currencies detected
    # User can accept automatic rates or enter manual ones
    conversion_rates = None
    if exchange_rates:
        conversion_rates = get_conversion_rates_from_user(exchange_rates, auto_yes=auto_yes)
        if conversion_rates is None:
            # User cancelled the operation
            print("\nConversion cancelled by user.")
            return False
    
    # Step 3: Build the list of output column names
    # Order: mandatory fields first, then tag fields as additional columns
    target_fields = []
    for target_field in mandatory_mapping.keys():
        # Handle typo in config: "ChageDescription" should be "ChargeDescription"
        if target_field == "ChageDescription":
            # Fix typo: use correct field name in output
            target_fields.append("ChargeDescription")
        else:
            target_fields.append(target_field)
    
    # Add tag fields as additional columns after mandatory fields
    tag_fields = list(tags_mapping.keys())
    all_target_fields = target_fields + tag_fields
    
    try:
        # Step 4: Open and read the input CSV file
        with open(input_file_path, 'r', encoding='utf-8') as infile:
            # Auto-detect CSV delimiter (handles comma, semicolon, tab, etc.)
            sample = infile.read(1024)
            infile.seek(0)  # Reset file pointer
            sniffer = csv.Sniffer()
            delimiter = sniffer.sniff(sample).delimiter
            
            # Create CSV reader that treats first row as column headers
            reader = csv.DictReader(infile, delimiter=delimiter)
            
            # Step 5: Validate that all required source columns exist in the CSV
            csv_headers = reader.fieldnames
            if csv_headers is None:
                print("Error: Could not read CSV headers.")
                return False
            
            missing_fields = []
            
            # Check that all mandatory source fields exist in the CSV
            for target_field, source_field in mandatory_mapping.items():
                if source_field and source_field not in csv_headers:
                    # Use correct field name for display (handle typo)
                    display_field = "ChargeDescription" if target_field == "ChageDescription" else target_field
                    missing_fields.append(f"{display_field} (source: {source_field})")
            
            # Check that all tag source fields exist in the CSV
            for tag_field, source_field in tags_mapping.items():
                if source_field and source_field not in csv_headers:
                    missing_fields.append(f"{tag_field} (source: {source_field})")
            
            # If any required fields are missing, show error and available fields
            if missing_fields:
                print(f"Warning: The following fields are missing in the source CSV:")
                for field in missing_fields:
                    print(f"  - {field}")
                print("\nAvailable fields in source CSV:")
                for header in csv_headers:
                    print(f"  - {header}")
                return False
            
            # Step 6: Create output directory if it doesn't exist
            output_dir = Path(output_file_path).parent
            output_dir.mkdir(parents=True, exist_ok=True)
            
            # Step 7: Process all rows and convert to JSON format
            all_output_rows = []
            row_count = 0
            converted_count = 0  # Track how many rows had currency conversion
            
            # Process each row from the input CSV
            for row in reader:
                output_row = {}
                
                # Extract current currency and cost values for this row
                current_currency = row.get(billing_currency_source, "").strip().upper()
                current_cost_str = row.get(billed_cost_source, "")
                
                # Step 8: Map mandatory fields from source to target columns
                for target_field, source_field in mandatory_mapping.items():
                    if target_field == "BilledCost":
                        # Special handling: Convert cost to USD if currency conversion is needed
                        if conversion_rates and current_currency in conversion_rates and current_currency != "USD":
                            try:
                                # Parse cost as float and apply conversion rate
                                current_cost = float(current_cost_str) if current_cost_str else 0.0
                                conversion_rate = conversion_rates[current_currency]
                                usd_cost = current_cost * conversion_rate
                                # Store as float for JSON
                                output_row[target_field] = usd_cost
                                converted_count += 1
                            except (ValueError, TypeError):
                                # If cost can't be parsed as number, keep original value as string
                                try:
                                    output_row[target_field] = float(current_cost_str) if current_cost_str else 0.0
                                except (ValueError, TypeError):
                                    output_row[target_field] = current_cost_str
                        else:
                            # No conversion needed, try to convert to float if possible
                            try:
                                output_row[target_field] = float(current_cost_str) if current_cost_str else 0.0
                            except (ValueError, TypeError):
                                output_row[target_field] = current_cost_str
                    elif target_field == "BillingCurrency":
                        # Special handling: Set to USD if conversion was applied
                        if conversion_rates and current_currency in conversion_rates and current_currency != "USD":
                            # Currency was converted, so set to USD
                            output_row[target_field] = "USD"
                        else:
                            # No conversion, keep original currency
                            output_row[target_field] = row.get(source_field, "")
                    elif target_field in ["ChargePeriodStart", "ChargePeriodEnd"]:
                        # Special handling: Format date fields to YYYY-MM-DD
                        date_value = row.get(source_field, "")
                        output_row[target_field] = format_iso_to_yyyy_mm_dd(date_value)
                    else:
                        # Standard field mapping: copy value from source to target
                        output_row[target_field] = row.get(source_field, "")
                
                # Step 9: Add tag fields in a dedicated "Tags" object (OUTSIDE the mandatory fields loop)
                # Group all tag fields into a Tags dictionary
                tags_dict = {}
                for tag_field, source_field in tags_mapping.items():
                    tag_value = row.get(source_field, "")
                    if tag_value:  # Only include non-empty tags
                        tags_dict[tag_field] = tag_value
                
                # Add Tags object to output row (only if there are tags)
                if tags_dict:
                    output_row["Tags"] = tags_dict
                
                # Add row to list for JSON output
                all_output_rows.append(output_row)
                row_count += 1
            
            # Step 10: Write the converted data as JSON file
            with open(output_file_path, 'w', encoding='utf-8') as outfile:
                json.dump(all_output_rows, outfile, indent=2, ensure_ascii=False)
            
            # Step 11: Display conversion summary
            print(f"\nSuccessfully converted {row_count} rows.")
            if conversion_rates and converted_count > 0:
                print(f"  → {converted_count} rows converted to USD")
            print(f"Output file: {output_file_path}")
            print(f"Output columns: {len(all_target_fields)} ({len(target_fields)} mandatory + {len(tag_fields)} tags)")
            return True
                
    except FileNotFoundError:
        print(f"Error: Input file '{input_file_path}' not found.")
        return False
    except Exception as e:
        print(f"Error processing CSV file: {e}")
        return False

def list_datadog_cost_files(datadog_config, limit=10):
    """List the last cost files uploaded to Datadog Cloud Cost Management.
    
    Uses the Datadog API client library to retrieve information about cost files that have been
    uploaded to the platform. Returns the most recent files.
    
    Args:
        datadog_config: Dictionary containing Datadog API credentials
            - api_key: Datadog API key
            - app_key: Datadog Application key
            - site: Datadog site URL (e.g., "datadoghq.eu", "datadoghq.com")
        limit: Maximum number of files to return (default: 10)
        
    Returns:
        List of cost file information dictionaries, or None if error occurred
    """
    if datadog_config is None:
        return None
    
    # Check if datadog_api_client is available
    if not DATADOG_API_AVAILABLE:
        print("Error: datadog_api_client library is not installed.")
        print("Please install it with: pip install datadog-api-client")
        return None
    
    api_key = datadog_config.get("api_key")
    app_key = datadog_config.get("app_key")
    site = datadog_config.get("site", "datadoghq.com")
    
    # Validate that required credentials are present
    if not api_key or not app_key:
        print("Error: Datadog API key or Application key is missing in configuration.")
        return None
    
    try:
        # Configure Datadog API client
        configuration = Configuration()
        configuration.api_key["apiKeyAuth"] = api_key
        configuration.api_key["appKeyAuth"] = app_key
        configuration.server_variables["site"] = site
        
        # Create API client instance
        with ApiClient(configuration) as api_client:
            # Initialize Cloud Cost Management API
            api_instance = cloud_cost_management_api.CloudCostManagementApi(api_client)
            
            # List cost files using the Cloud Cost Management API
            # Request the first page (page_number=0) with sorting by uploaded_at descending (most recent first)
            try:
                # Try to call list_custom_costs_files with sorting and pagination parameters
                try:
                    # Use page_number=0 for first page (most recent files)
                    # Sort by uploaded_at in descending order to get newest files first
                    response = api_instance.list_custom_costs_files(
                        page_size=limit,
                        sort="-created_at",
                        page_number=0
                    )
                except TypeError as e:
                    print(f"Error calling list_custom_costs_files: {e}")
            except AttributeError:
                print("Error: list_custom_costs_files method not found in Cloud Cost Management API.")
                print("  Please check Datadog API documentation for the correct method name.")
                return None
            except Exception as e:
                print(f"Error calling list_custom_costs_files: {e}")
                return None
            
            try:
                # Extract files from response
                # Response structure depends on API version
                files = None
                
                if hasattr(response, 'data') and response.data:
                    # Response has a 'data' attribute
                    files = response.data
                elif hasattr(response, 'files') and response.files:
                    # Response has a 'files' attribute
                    files = response.files
                elif isinstance(response, list):
                    # Response is directly a list
                    files = response
                elif hasattr(response, '__dict__'):
                    # Try to extract from response dictionary
                    response_dict = response.__dict__
                    if 'data' in response_dict:
                        files = response_dict['data']
                    elif 'files' in response_dict:
                        files = response_dict['files']
                
                if files:
                    # Convert to list if needed
                    if not isinstance(files, list):
                        files = [files] if files else []
                    
                    # Sort by date (most recent first)
                    # Files may have attributes in _data_store.attributes or directly accessible
                    try:
                        def get_file_date(file_item):
                            """Extract date from file item for sorting (most recent first).
                            
                            Tries to find a date field in the file attributes, prioritizing:
                            1. created_at or uploaded_at (upload/creation date)
                            2. charge_period.end (end of charge period - most recent period)
                            3. Other date fields
                            """
                            attributes = None
                            
                            # Try to access attributes via _data_store
                            if isinstance(file_item, dict):
                                # Check _data_store.attributes path
                                if '_data_store' in file_item:
                                    data_store = file_item['_data_store']
                                    if isinstance(data_store, dict) and 'attributes' in data_store:
                                        attributes = data_store['attributes']
                            elif hasattr(file_item, '_data_store'):
                                data_store = file_item._data_store
                                if isinstance(data_store, dict):
                                    attributes = data_store.get('attributes', {})
                                elif hasattr(data_store, 'attributes'):
                                    attributes = data_store.attributes
                            
                            if attributes and isinstance(attributes, dict):
                                # Priority 1: Upload/creation dates (most accurate for "last uploaded")
                                for field in ["created_at", "uploaded_at", "date", "timestamp", "upload_date", "created"]:
                                    if field in attributes:
                                        date_value = attributes[field]
                                        # Return as string for comparison (ISO format dates sort correctly as strings)
                                        return str(date_value) if date_value else ""
                                
                                # Priority 2: Charge period end date (most recent charge period)
                                if "charge_period" in attributes:
                                    charge_period = attributes["charge_period"]
                                    if isinstance(charge_period, dict) and "end" in charge_period:
                                        end_date = charge_period["end"]
                                        return str(end_date) if end_date else ""
                            
                            # Fallback: Try direct access if it's a dict
                            if isinstance(file_item, dict):
                                for field in ["created_at", "uploaded_at", "date", "timestamp", "upload_date", "created"]:
                                    if field in file_item:
                                        return str(file_item[field]) if file_item[field] else ""
                            
                            # Return empty string if no date found (will sort to end)
                            return ""
                        
                        # Sort files by date in descending order (most recent first)
                        files.sort(key=get_file_date, reverse=True)
                    except Exception as sort_error:
                        # If sorting fails, keep original order
                        print(f"Warning: Could not sort files by date: {sort_error}")
                        pass
                    
                    # Limit results to get the most recent files
                    result_files = files[:limit]
                    
                    # Convert objects to dictionaries for easier handling
                    file_list = []
                    for file_item in result_files:
                        if isinstance(file_item, dict):
                            file_list.append(file_item)
                        elif hasattr(file_item, '__dict__'):
                            file_list.append(file_item.__dict__)
                        else:
                            # Try to convert to dict using to_dict if available
                            if hasattr(file_item, 'to_dict'):
                                file_list.append(file_item.to_dict())
                            else:
                                file_list.append({"name": str(file_item)})
                    
                    return file_list
                else:
                    print("No cost files found in Datadog response.")
                    return []
                    
            except Exception as api_error:
                # Handle API-specific errors
                error_msg = str(api_error)
                if "403" in error_msg or "Forbidden" in error_msg:
                    print("Error: Access forbidden. Please check your API key permissions.")
                elif "401" in error_msg or "Unauthorized" in error_msg:
                    print("Error: Unauthorized. Please check your API and Application keys.")
                else:
                    print(f"Error calling Datadog API: {api_error}")
                return None
                
    except Exception as e:
        print(f"Unexpected error connecting to Datadog: {e}")
        return None

def display_datadog_cost_files(datadog_config, config=None):
    """Display cost files from Datadog.
    
    Loads Datadog configuration, fetches cost files, and displays them
    in a formatted list. The number of files to display is read from config.
    
    Args:
        datadog_config: Dictionary containing Datadog API credentials
        config: Main configuration dictionary (optional, for reading limit value)
    """
    # Get limit from config file, default to 10 if not specified
    limit = 10
    if config and "DatadogCostFilesLimit" in config:
        try:
            limit = int(config.get("DatadogCostFilesLimit", 10))
        except (ValueError, TypeError):
            print("Warning: Invalid DatadogCostFilesLimit in config, using default value of 10")
            limit = 10
    
    print("\n" + "=" * 60)
    print("Fetching cost files from Datadog...")
    print("=" * 60)
    
    cost_files = list_datadog_cost_files(datadog_config, limit=limit)
    
    if cost_files is None:
        print("Could not retrieve cost files from Datadog.")
        print("Please check your API credentials in .datadog.json")
        return
    
    if not cost_files or len(cost_files) == 0:
        print("No cost files found on Datadog.")
        return
    
    print(f"\nLast {len(cost_files)} cost files on Datadog:")
    print("-" * 60)
    
    # Display files - adjust field names based on actual API response structure
    for idx, file_info in enumerate(cost_files, start=1):
        # Try to extract common fields (adjust based on actual API response)
        file_attributes=file_info.get("_data_store").get("attributes")
        file_name = file_attributes.get("name") or file_info.get("filename") or file_info.get("id", "Unknown")
        file_date = file_attributes.get("date") or file_info.get("created_at") or file_info.get("uploaded_at", "Unknown")
        file_chargeperiod_start=file_attributes.get("charge_period").get("start") or file_info.get("charge_period_start", "Unknown")
        file_chargeperiod_end=file_attributes.get("charge_period").get("end") or file_info.get("charge_period_end", "Unknown")
        file_size = file_attributes.get("size") or file_info.get("file_size", "Unknown")
        
        print(f"  {idx}. {file_name}")
        if file_date != "Unknown":
            print(f"      Date: {file_date}")
        if file_size != "Unknown":
            print(f"      Size: {file_size}")
    
    print("-" * 60)

def extract_charge_period_from_json(json_file_path):
    """Extract charge period (start and end dates) from the converted JSON file.
    
    Reads the JSON file and finds the earliest ChargePeriodStart and latest ChargePeriodEnd
    to determine the overall charge period covered by the file.
    
    Args:
        json_file_path: Path to the converted JSON file
        
    Returns:
        Tuple of (start_date, end_date) as strings in YYYY-MM-DD format, or (None, None) if not found
    """
    try:
        with open(json_file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
            if not isinstance(data, list):
                return (None, None)
            
            start_dates = []
            end_dates = []
            
            for row in data:
                if not isinstance(row, dict):
                    continue
                    
                start_date = row.get("ChargePeriodStart")
                end_date = row.get("ChargePeriodEnd")
                
                if start_date:
                    start_date_str = str(start_date).strip()
                    if start_date_str:
                        start_dates.append(start_date_str)
                if end_date:
                    end_date_str = str(end_date).strip()
                    if end_date_str:
                        end_dates.append(end_date_str)
            
            if start_dates and end_dates:
                # Find earliest start and latest end
                earliest_start = min(start_dates)
                latest_end = max(end_dates)
                return (earliest_start, latest_end)
            else:
                return (None, None)
                
    except Exception as e:
        print(f"Error extracting charge period from JSON: {e}")
        return (None, None)

def periods_overlap(start1, end1, start2, end2):
    """Check if two date periods overlap.
    
    Args:
        start1, end1: First period (start and end dates as strings in YYYY-MM-DD format)
        start2, end2: Second period (start and end dates as strings)
        
    Returns:
        True if periods overlap, False otherwise
    """
    try:
        # Parse dates (assuming YYYY-MM-DD format or ISO format)
        def parse_date(date_str):
            # Remove timezone info if present
            date_str = date_str.split('T')[0] if 'T' in date_str else date_str
            date_str = date_str.split('.')[0] if '.' in date_str else date_str
            # Try YYYY-MM-DD format first
            try:
                return datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                # Try other formats
                for fmt in ["%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y"]:
                    try:
                        return datetime.strptime(date_str, fmt)
                    except ValueError:
                        continue
                raise ValueError(f"Could not parse date: {date_str}")
        
        d1_start = parse_date(start1)
        d1_end = parse_date(end1)
        d2_start = parse_date(start2)
        d2_end = parse_date(end2)
        
        # Periods overlap if: start1 <= end2 AND start2 <= end1
        return d1_start <= d2_end and d2_start <= d1_end
        
    except Exception as e:
        print(f"Warning: Could not parse dates for overlap check: {e}")
        return False

def find_overlapping_datadog_files(datadog_config, csv_start_date, csv_end_date, limit=100):
    """Find Datadog cost files whose charge period overlaps with the CSV file period.
    Excludes JSON files from the results.
    
    Args:
        datadog_config: Dictionary containing Datadog API credentials
        csv_start_date: Start date of charge period in CSV (string in YYYY-MM-DD format)
        csv_end_date: End date of charge period in CSV (string in YYYY-MM-DD format)
        limit: Maximum number of files to check (default: 100)
        
    Returns:
        List of file information dictionaries that overlap (excluding JSON files), or None if error
    """
    if not csv_start_date or not csv_end_date:
        print("Error: Could not determine charge period from CSV file.")
        return None
    
    # Get all cost files from Datadog
    all_files = list_datadog_cost_files(datadog_config, limit=limit)
    
    if all_files is None or not all_files:
        return []
    
    overlapping_files = []
    
    for file_info in all_files:
        try:
            # Extract file name and attributes
            file_attributes = None
            file_name = None
            
            if isinstance(file_info, dict):
                if '_data_store' in file_info:
                    data_store = file_info['_data_store']
                    if isinstance(data_store, dict) and 'attributes' in data_store:
                        file_attributes = data_store['attributes']
                        file_name = file_attributes.get("name", "")
            
            # Skip non-JSON files and non active files
            #GL if file_name and (not file_name.lower().endswith('.json') or not ((file_attributes.get("_data_store").get("status") in ["ACTIVE","PROCESSING","UPLOADING"]))):
            if file_name and not ((file_attributes.get("_data_store").get("status") in ["ACTIVE","PROCESSING","UPLOADING"])):
                continue
            
            # Extract charge period from Datadog file
            if file_attributes :
                charge_period = file_attributes.get("charge_period")
                if charge_period :
                    file_start = charge_period.get("start")
                    file_end = charge_period.get("end")
                    
                    if file_start and file_end:
                        # Format dates to YYYY-MM-DD for comparison
                        file_start_formatted = format_epochmillis_to_yyyy_mm_dd(file_start)
                        file_end_formatted = format_epochmillis_to_yyyy_mm_dd(file_end)
                        
                        # Check if periods overlap
                        if periods_overlap(csv_start_date, csv_end_date, file_start_formatted, file_end_formatted):
                            overlapping_files.append(file_info)
        except Exception as e:
            print(f"Error processing file {file_name}: {e}")
            continue
    
    return overlapping_files

def extract_datadog_file_content(datadog_config, file_info):
    """Extract content from a Datadog cost file without writing to disk.
    
    Args:
        datadog_config: Dictionary containing Datadog API credentials
        file_info: File information dictionary from Datadog API
        
    Returns:
        List of dictionaries representing file content, or None if error
    """
    if not DATADOG_API_AVAILABLE:
        return None
    
    try:
        # Extract file ID
        file_id = None
        if isinstance(file_info, dict):
            if '_data_store' in file_info:
                data_store = file_info['_data_store']
                if isinstance(data_store, dict):
                    file_id = data_store.get('id')
                elif hasattr(data_store, 'id'):
                    file_id = data_store.id
        
        if not file_id:
            return None
        
        # Configure Datadog API client
        api_key = datadog_config.get("api_key")
        app_key = datadog_config.get("app_key")
        site = datadog_config.get("site", "datadoghq.com")
        
        configuration = Configuration()
        configuration.api_key["apiKeyAuth"] = api_key
        configuration.api_key["appKeyAuth"] = app_key
        configuration.server_variables["site"] = site
        
        # Get file content using Datadog API
        with ApiClient(configuration) as api_client:
            api_instance = cloud_cost_management_api.CloudCostManagementApi(api_client)
            
            try:
                # Get file metadata and data using get_custom_costs_file
                file_response = api_instance.get_custom_costs_file(file_id)
                
                # Extract file content from nested _data_store structure
                file_content = None
                
                try:
                    # Access the nested structure
                    if hasattr(file_response, '_data_store') or isinstance(file_response, dict):
                        response_dict = file_response._data_store if hasattr(file_response, '_data_store') else file_response
                        
                        if isinstance(response_dict, dict):
                            data_obj = response_dict.get('data')
                            
                            if data_obj:
                                # Get _data_store from data object
                                if hasattr(data_obj, '_data_store'):
                                    data_store = data_obj._data_store
                                elif isinstance(data_obj, dict):
                                    data_store = data_obj.get('_data_store', data_obj)
                                else:
                                    data_store = data_obj
                                
                                if data_store:
                                    # Get attributes
                                    if isinstance(data_store, dict):
                                        attributes = data_store.get('attributes', data_store)
                                    elif hasattr(data_store, 'attributes'):
                                        attributes = data_store.attributes
                                    elif hasattr(data_store, '_data_store'):
                                        attributes = data_store._data_store
                                    else:
                                        attributes = data_store
                                    
                                    if attributes:
                                        # Get content from attributes
                                        if isinstance(attributes, dict):
                                            file_content = attributes.get('content')
                                        elif hasattr(attributes, 'content'):
                                            file_content = attributes.content
                                        elif hasattr(attributes, '_data_store'):
                                            content_store = attributes._data_store
                                            if isinstance(content_store, dict):
                                                file_content = content_store.get('content')
                                            elif hasattr(content_store, 'content'):
                                                file_content = content_store.content
                
                except Exception as extract_error:
                    print(f"Error extracting file content: {extract_error}")
                    return None
                
                # Return content if it's a list of dictionaries
                if isinstance(file_content, list):
                    return file_content
                else:
                    return None
                    
            except Exception as e:
                print(f"Error getting file content: {e}")
                return None
                
    except Exception as e:
        return None

def download_datadog_file(datadog_config, file_info, output_directory="tmp/datadog_downloads"):
    """Download a cost file from Datadog.
    
    Args:
        datadog_config: Dictionary containing Datadog API credentials
        file_info: File information dictionary from Datadog API
        output_directory: Directory where to save the downloaded file
        
    Returns:
        Path to downloaded file if successful, None otherwise
    """
    if not DATADOG_API_AVAILABLE:
        print("Error: datadog_api_client library is not installed.")
        return None
    
    try:
        # Extract file ID and name
        file_id = None
        file_name = None
        file_attributes = None
        
        if isinstance(file_info, dict):
            file_id = file_info.get("_data_store").get("id")
            if '_data_store' in file_info:
                data_store = file_info['_data_store']
                if isinstance(data_store, dict) and 'attributes' in data_store:
                    file_attributes = data_store['attributes']
                    file_name = file_attributes.get("name")
        
        if not file_id:
            print("Error: Could not extract file ID from file information.")
            return None
        
        # Configure Datadog API client
        api_key = datadog_config.get("api_key")
        app_key = datadog_config.get("app_key")
        site = datadog_config.get("site", "datadoghq.com")
        
        configuration = Configuration()
        configuration.api_key["apiKeyAuth"] = api_key
        configuration.api_key["appKeyAuth"] = app_key
        configuration.server_variables["site"] = site
        
        # Create output directory
        output_dir = Path(output_directory)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate output filename
        if file_name:
            output_filename = file_name
        else:
            output_filename = f"datadog_file_{file_id}.csv"
        
        output_path = output_dir / output_filename
        
        # Download file using Datadog API
        with ApiClient(configuration) as api_client:
            api_instance = cloud_cost_management_api.CloudCostManagementApi(api_client)
            
            # Try to get the file metadata and content
            try:
                # Get file metadata and data using get_custom_costs_file
                file_response = api_instance.get_custom_costs_file(file_id)
                
                # Extract file content from nested _data_store structure
                # Path: _data_store['data']._data_store['attributes']._data_store['content']
                file_content = None
                
                try:
                    # Access the nested structure
                    if hasattr(file_response, '_data_store') or isinstance(file_response, dict):
                        response_dict = file_response._data_store if hasattr(file_response, '_data_store') else file_response
                        
                        if isinstance(response_dict, dict):
                            data_obj = response_dict.get('data')
                            
                            if data_obj:
                                # Get _data_store from data object
                                if hasattr(data_obj, '_data_store'):
                                    data_store = data_obj._data_store
                                elif isinstance(data_obj, dict):
                                    data_store = data_obj.get('_data_store', data_obj)
                                else:
                                    data_store = data_obj
                                
                                if data_store:
                                    # Get attributes
                                    if isinstance(data_store, dict):
                                        attributes = data_store.get('attributes', data_store)
                                    elif hasattr(data_store, 'attributes'):
                                        attributes = data_store.attributes
                                    elif hasattr(data_store, '_data_store'):
                                        attributes = data_store._data_store
                                    else:
                                        attributes = data_store
                                    
                                    if attributes:
                                        # Get content from attributes
                                        if isinstance(attributes, dict):
                                            file_content = attributes.get('content')
                                        elif hasattr(attributes, 'content'):
                                            file_content = attributes.content
                                        elif hasattr(attributes, '_data_store'):
                                            content_store = attributes._data_store
                                            if isinstance(content_store, dict):
                                                file_content = content_store.get('content')
                                            elif hasattr(content_store, 'content'):
                                                file_content = content_store.content
                
                except Exception as extract_error:
                    print(f"Error extracting file content from response structure: {extract_error}")
                    return None
                
                if file_content is None:
                    print(f"Error: Could not extract file content from response for file {file_id}")
                    return None
                
                # Convert content (list of dictionaries) to CSV
                try:
                    # Check if content is a list of dictionaries
                    if isinstance(file_content, list) and len(file_content) > 0:
                        # Get all unique keys from all rows (including nested Tags)
                        all_keys = set()
                        tag_keys = set()
                        
                        for row in file_content:
                            #GL all_keys=row.keys()
                            if 'Tags' in row and isinstance(row['Tags'], dict):
                                tag_keys.update(row['Tags'].keys())
                        
                        # Build column names: main fields first, then tag fields
                        main_fields = ['ProviderName', 'ChargeDescription', 'ChargePeriodStart', 
                                      'ChargePeriodEnd', 'BilledCost', 'BillingCurrency']
                        
                        #GL # Remove Tags from main fields if present
                        #GL main_fields = [f for f in main_fields if f in all_keys]

                        # Add other main fields that aren't Tags
                        other_fields = [f for f in all_keys if f != 'Tags' and f not in main_fields]
                        # Tag fields
                        tag_fields = sorted(tag_keys)
                        
                        # Combine all column names
                        fieldnames = main_fields + other_fields + tag_fields
                        
                        # Write CSV file
                        with open(output_path, 'w', encoding='utf-8', newline='') as csvfile:
                            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                            writer.writeheader()
                            
                            for row in file_content:
                                csv_row = {}
                                
                                # Copy main fields
                                for field in main_fields + other_fields:
                                    if field in row:
                                        csv_row[field] = row[field]
                                    else:
                                        csv_row[field] = ""
                                
                                # Flatten Tags dictionary into separate columns
                                if 'Tags' in row and isinstance(row['Tags'], dict):
                                    for tag_key in tag_fields:
                                        csv_row[tag_key] = row['Tags'].get(tag_key, "")
                                else:
                                    # Fill tag columns with empty values if no Tags
                                    for tag_key in tag_fields:
                                        csv_row[tag_key] = ""
                                
                                writer.writerow(csv_row)
                        
                        print(f"  ✓ Downloaded and converted to CSV: {output_filename}")
                        return output_path
                    
                    else:
                        # If content is not a list of dictionaries, try to write as-is
                        if isinstance(file_content, bytes):
                            # Binary content
                            with open(output_path, 'wb') as f:
                                f.write(file_content)
                        elif isinstance(file_content, str):
                            # String content (CSV text)
                            with open(output_path, 'w', encoding='utf-8') as f:
                                f.write(file_content)
                        elif hasattr(file_content, 'read'):
                            # File-like object
                            with open(output_path, 'wb') as f:
                                f.write(file_content.read())
                        else:
                            # Try to convert to string
                            content_str = str(file_content)
                            with open(output_path, 'w', encoding='utf-8') as f:
                                f.write(content_str)
                        
                        print(f"  ✓ Downloaded: {output_filename}")
                        return output_path
                    
                except Exception as write_error:
                    print(f"Error writing file {output_filename}: {write_error}")
                    import traceback
                    traceback.print_exc()
                    return None
                
            except AttributeError:
                print(f"Error: get_custom_costs_file method not found for file {file_id}")
                return None
            except Exception as e:
                print(f"Error downloading file {file_id}: {e}")
                return None
                
    except Exception as e:
        print(f"Error downloading Datadog file: {e}")
        return None

def download_overlapping_datadog_files(datadog_config, json_file_path, limit=20, temp_directory="tmp"):
    """Download and combine Datadog cost files that overlap with the converted JSON file's charge period.
    Combines all file contents into a single JSON file.
    
    Args:
        datadog_config: Dictionary containing Datadog API credentials
        json_file_path: Path to the converted JSON file
        limit: Maximum number of files to process
        temp_directory: Directory for temporary files (default: "tmp")
    """
    print("\n" + "=" * 60)
    print("Finding overlapping Datadog cost files...")
    print("=" * 60)
    
    # Extract charge period from JSON
    csv_start, csv_end = extract_charge_period_from_json(json_file_path)
    
    if not csv_start or not csv_end:
        print("Error: Could not extract charge period from JSON file.")
        return
    
    print(f"CSV charge period: {csv_start} to {csv_end}")
    
    # Find overlapping files (only CSV files)
    overlapping_files = find_overlapping_datadog_files(datadog_config, csv_start, csv_end, limit=limit)
    
    if overlapping_files is None:
        print("Error: Could not retrieve files from Datadog.")
        return
    
    if not overlapping_files:
        print("No overlapping files found on Datadog (only CSV files).")
        return
    
    print(f"\nFound {len(overlapping_files)} overlapping file(s) (only CSV).")
    print("Extracting and combining file contents...")
    print("-" * 60)
    
    # Collect all content from all files
    all_content = []
    processed_count = 0
    
    for file_info in overlapping_files:
        # Extract file name and charge period for display
        file_name = "Unknown"
        charge_period_start = "Unknown"
        charge_period_end = "Unknown"
        
        try:
            if isinstance(file_info, dict):
                if '_data_store' in file_info:
                    data_store = file_info['_data_store']
                    if isinstance(data_store, dict) and 'attributes' in data_store:
                        file_attributes = data_store['attributes']
                        file_name = file_attributes.get("name", "Unknown")
                        charge_period = file_attributes.get("charge_period")
                        if charge_period:
                            period_start = charge_period.get("start")
                            period_end = charge_period.get("end")
                            if period_start:
                                try:
                                    charge_period_start = format_epochmillis_to_yyyy_mm_dd(period_start)
                                except (ValueError, TypeError):
                                    charge_period_start = str(period_start)
                            if period_end:
                                try:
                                    charge_period_end = format_epochmillis_to_yyyy_mm_dd(period_end)
                                except (ValueError, TypeError):
                                    charge_period_end = str(period_end)
        except Exception as e:
            # If extraction fails, continue with defaults
            pass
        
        file_content = extract_datadog_file_content(datadog_config, file_info)
        if file_content and isinstance(file_content, list):
            all_content.extend(file_content)
            processed_count += 1
            # Display file name and charge period
            print(f"  ✓ Extracted content from file {processed_count}/{len(overlapping_files)}: {file_name}")
            if charge_period_start != "Unknown" and charge_period_end != "Unknown":
                print(f"      Charge period: {charge_period_start} to {charge_period_end}")
            elif charge_period_start != "Unknown" or charge_period_end != "Unknown":
                print(f"      Charge period: {charge_period_start} to {charge_period_end}")
    
    if not all_content:
        print("No content extracted from files.")
        return
    
    # Write combined content to single JSON file
    output_path = Path(temp_directory) / "datadog_content.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        # Helper function to convert various object types to a simple dictionary
        def convert_to_dict(obj):
            """Convert various object types (UnparsedObject, dict, etc.) to a simple dictionary.
            
            Handles UnparsedObject from datadog_api_client by accessing its internal structure.
            """
            # Already a dict, return as-is
            if isinstance(obj, dict):
                return obj
            
            # Handle UnparsedObject - check for the class name or specific attributes
            obj_type = type(obj).__name__
            if 'Unparsed' in obj_type or hasattr(obj, '_data_store'):
                # Try _data_store first (common in datadog_api_client objects)
                if hasattr(obj, '_data_store'):
                    data_store = obj._data_store
                    if isinstance(data_store, dict):
                        # Recursively convert nested values
                        result = {}
                        for key, value in data_store.items():
                            if isinstance(value, (dict, list)):
                                result[key] = convert_nested_value(value)
                            else:
                                result[key] = value
                        return result
                
                # Try to_dict method
                if hasattr(obj, 'to_dict'):
                    try:
                        result = obj.to_dict()
                        if isinstance(result, dict):
                            return result
                    except:
                        pass
                
                # Try __dict__ attribute
                if hasattr(obj, '__dict__'):
                    result = {}
                    for key, value in obj.__dict__.items():
                        # Skip private attributes and _data_store if already processed
                        if not key.startswith('_') or key == '_data_store':
                            if isinstance(value, (dict, list)):
                                result[key] = convert_nested_value(value)
                            else:
                                result[key] = value
                    return result
            
            # Try dict() constructor for iterable objects
            try:
                if hasattr(obj, 'keys') and hasattr(obj, '__getitem__'):
                    result = {}
                    for key in obj.keys():
                        result[key] = obj[key]
                    return result
            except:
                pass
            
            # Last resort: return empty dict
            return {}
        
        def convert_nested_value(value):
            """Recursively convert nested structures (lists, dicts) to simple Python types."""
            if isinstance(value, dict):
                return {k: convert_nested_value(v) for k, v in value.items()}
            elif isinstance(value, list):
                return [convert_nested_value(item) for item in value]
            elif hasattr(value, '_data_store'):
                # Handle nested UnparsedObject
                return convert_to_dict(value)
            else:
                return value
        
        # Get all unique keys from all rows (including nested Tags)
        all_keys = set()
        tag_keys = set()
        
        for row in all_content:
            # Convert row to a simple dictionary (handles UnparsedObject)
            row_dict = convert_to_dict(row)
            if not row_dict:
                continue
            
            all_keys.update(row_dict.keys())
            # Check both 'Tags' (our format) and 'tags' (Datadog format)
            if 'Tags' in row_dict and isinstance(row_dict['Tags'], dict):
                tag_keys.update(row_dict['Tags'].keys())
            if 'tags' in row_dict and isinstance(row_dict['tags'], dict):
                tag_keys.update(row_dict['tags'].keys())
    
        # Build column names: main fields first, then tag fields
        main_fields = ['ProviderName', 'ChargeDescription', 'ChargePeriodStart', 
                      'ChargePeriodEnd', 'BilledCost', 'BillingCurrency']
        
        # Add other main fields that aren't Tags
        other_fields = [f for f in all_keys if f != 'Tags' and f not in main_fields]
        # Tag fields
        tag_fields = sorted(tag_keys)
        
        # Convert all rows to simple dictionaries for JSON output
        json_rows = []
        
        for row in all_content:
            # Convert row to simple dict (handles UnparsedObject and other types)
            simple_row = convert_to_dict(row)
            if not simple_row:
                continue
            
            json_row = {}
            
            # Copy main fields
            for field in main_fields + other_fields:
                if field in simple_row:
                    json_row[field] = simple_row[field]
                else:
                    json_row[field] = None
            
            # Flatten Tags/tags dictionary into separate fields
            # Check both 'Tags' (our format) and 'tags' (Datadog format)
            tags_dict = None
            if 'Tags' in simple_row and isinstance(simple_row['Tags'], dict):
                tags_dict = simple_row['Tags']
            elif 'tags' in simple_row and isinstance(simple_row['tags'], dict):
                tags_dict = simple_row['tags']
            
            if tags_dict:
                for tag_key in tag_fields:
                    json_row[tag_key] = tags_dict.get(tag_key, None)
            else:
                # Fill tag fields with None if no Tags/tags
                for tag_key in tag_fields:
                    json_row[tag_key] = None
            
            json_rows.append(json_row)
        
        # Write combined JSON file
        with open(output_path, 'w', encoding='utf-8') as jsonfile:
            json.dump(json_rows, jsonfile, indent=2, ensure_ascii=False)
        
        print("-" * 60)
        print(f"✓ Combined {processed_count} file(s) into: {output_path}")
        print(f"  Total rows: {len(json_rows)}")
        
    except Exception as write_error:
        print(f"Error writing combined CSV file: {write_error}")
        import traceback
        traceback.print_exc()

def load_datadog_content_for_comparison(datadog_content_path="tmp/datadog_content.json", comparison_fields=None):
    """Load datadog_content.json and create a set of comparison keys.
    
    Reads the datadog_content.json file and creates a set of tuples representing
    unique combinations of the comparison fields. This set is used to identify
    duplicate rows in the converted JSON.
    
    Args:
        datadog_content_path: Path to the datadog_content.json file
        comparison_fields: List of field names to use for comparison
        
    Returns:
        Set of tuples representing unique row combinations, or None if file doesn't exist
    """
    if comparison_fields is None:
        comparison_fields = ["ProviderName", "ChargePeriodStart", "ChargePeriodEnd", "resourceid"]
    
    datadog_content_path = Path(datadog_content_path)
    
    if not datadog_content_path.exists():
        print(f"Warning: datadog_content.json not found at {datadog_content_path}")
        return None
    
    comparison_set = set()
    row_count = 0
    
    try:
        with open(datadog_content_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
            if not isinstance(data, list):
                print("Error: datadog_content.json should contain a list of objects")
                return None
            
            if len(data) == 0:
                print("Warning: datadog_content.json is empty")
                return set()
            
            # Collect all fieldnames from all rows (including Tags/tags) to ensure we find all fields
            # Some rows might have Tags while others don't, so we need to check multiple rows
            # Also handle both 'Tags' (our format) and 'tags' (Datadog format)
            # Also handle both PascalCase and snake_case field names
            fieldnames = set()
            tag_fieldnames = set()
            
            # Create mapping from PascalCase to snake_case for common fields
            field_mapping = {
                'ProviderName': 'provider_name',
                'ChargePeriodStart': 'charge_period_start',
                'ChargePeriodEnd': 'charge_period_end',
                'BilledCost': 'billed_cost',
                'BillingCurrency': 'billing_currency',
                'ChargeDescription': 'charge_description'
            }
            
            # Check first few rows to collect all possible field names
            rows_to_check = min(10, len(data))  # Check up to 10 rows or all rows if less than 10
            for i in range(rows_to_check):
                row = data[i]
                if not isinstance(row, dict):
                    continue
                # Add root level fields
                fieldnames.update(row.keys())
                # Add Tags fields if present (check both 'Tags' and 'tags' for compatibility)
                if 'Tags' in row and isinstance(row['Tags'], dict):
                    tag_fieldnames.update(row['Tags'].keys())
                if 'tags' in row and isinstance(row['tags'], dict):
                    tag_fieldnames.update(row['tags'].keys())
            
            # Combine all fieldnames (root level + tags)
            all_fieldnames = fieldnames.union(tag_fieldnames)
            
            # Check if all comparison fields exist in the JSON (either at root level or in Tags)
            # Also check for snake_case equivalents
            missing_fields = []
            for field in comparison_fields:
                if field not in all_fieldnames:
                    # Check if snake_case equivalent exists
                    if field in field_mapping:
                        snake_case_field = field_mapping[field]
                        if snake_case_field not in all_fieldnames:
                            missing_fields.append(field)
                    else:
                        missing_fields.append(field)
            
            if missing_fields:
                print(f"Warning: Comparison fields not found in datadog_content.json: {missing_fields}")
                print(f"Available root fields: {sorted(fieldnames)}")
                if tag_fieldnames:
                    print(f"Available tag fields: {sorted(tag_fieldnames)}")
                # Try to continue with available fields (including snake_case equivalents)
                available_fields = []
                for field in comparison_fields:
                    if field in all_fieldnames:
                        available_fields.append(field)
                    elif field in field_mapping:
                        # Check if snake_case equivalent exists
                        snake_case_field = field_mapping[field]
                        if snake_case_field in all_fieldnames:
                            available_fields.append(field)  # Keep PascalCase name for consistency
                
                if not available_fields:
                    print("Error: None of the comparison fields are available in datadog_content.json")
                    return None
                print(f"Using available fields for comparison: {available_fields}")
                comparison_fields = available_fields
            
            # Create mapping from PascalCase to snake_case for common fields
            field_mapping = {
                'ProviderName': 'provider_name',
                'ChargePeriodStart': 'charge_period_start',
                'ChargePeriodEnd': 'charge_period_end',
                'BilledCost': 'billed_cost',
                'BillingCurrency': 'billing_currency',
                'ChargeDescription': 'charge_description'
            }
            
            for row in data:
                row_count += 1
                # Create a tuple with values from comparison fields
                comparison_values = []
                for field in comparison_fields:
                    value = None
                    # Check if field is in Tags/tags object (case-insensitive) or at root level
                    # Try 'Tags' first (our converted format), then 'tags' (Datadog format)
                    if 'Tags' in row and isinstance(row['Tags'], dict) and field in row['Tags']:
                        value = row['Tags'].get(field)
                    elif 'tags' in row and isinstance(row['tags'], dict) and field in row['tags']:
                        value = row['tags'].get(field)
                    else:
                        # Check at root level - try PascalCase first, then snake_case
                        value = row.get(field)
                        # If value is None or empty, try snake_case equivalent
                        if (value is None or value == "" or value == "null") and field in field_mapping:
                            snake_case_field = field_mapping[field]
                            value = row.get(snake_case_field)
                    
                    # Convert to string, handling None
                    if value is None:
                        value = ""
                    else:
                        value = str(value).strip()
                    comparison_values.append(value)
                
                # Add tuple to set (tuples are hashable)
                # Only add if at least one field has a value (avoid empty tuples)
                if any(comparison_values):
                    comparison_set.add(tuple(comparison_values))
        
        if row_count == 0:
            print("Warning: datadog_content.json is empty or has no data rows")
            return set()  # Return empty set instead of None
        
        print(f"Loaded {len(comparison_set)} unique comparison keys from {row_count} rows in datadog_content.json")
        return comparison_set
        
    except Exception as e:
        print(f"Error loading datadog_content.json for comparison: {e}")
        import traceback
        traceback.print_exc()
        return None

def clean_json_file(json_file_path, comparison_fields=None, datadog_content_path="tmp/datadog_content.json", temp_directory=None):
    """Remove duplicate lines from JSON file that already exist in datadog_content.json.
    
    Compares rows using the specified comparison fields and removes duplicates.
    Writes the cleaned version back to the file.
    
    Args:
        json_file_path: Path to the JSON file to clean
        comparison_fields: List of field names to use for comparison
        datadog_content_path: Path to the datadog_content.json file
        temp_directory: Directory for temporary files (default: "tmp")
        
    Returns:
        Number of rows removed, or -1 if error occurred
    """
    if comparison_fields is None:
        comparison_fields = ["ProviderName", "ChargePeriodStart", "ChargePeriodEnd", "resourceid"]
    
    # Load comparison set from datadog_content.json
    comparison_set = load_datadog_content_for_comparison(datadog_content_path, comparison_fields)
    
    if comparison_set is None:
        print("Warning: Could not load datadog_content.json. Skipping deduplication.")
        return 0
    
    if len(comparison_set) == 0:
        print("Warning: datadog_content.json is empty. No duplicates to remove.")
        return 0
    
    json_file_path = Path(json_file_path)
    
    if not json_file_path.exists():
        print(f"Error: JSON file not found: {json_file_path}")
        return -1
    
    try:
        # Read original JSON
        with open(json_file_path, 'r', encoding='utf-8') as f:
            original_rows = json.load(f)
        
        if not isinstance(original_rows, list):
            print("Error: JSON file should contain a list of objects.")
            return -1
        
        if len(original_rows) == 0:
            print("Warning: JSON file is empty.")
            return 0
        
        # Get fieldnames from first row (including fields in Tags object)
        first_row = original_rows[0]
        if not isinstance(first_row, dict):
            print("Error: JSON file should contain a list of dictionaries.")
            return -1
        
        fieldnames = list(first_row.keys())
        # Also check Tags object if present
        if 'Tags' in first_row and isinstance(first_row['Tags'], dict):
            tag_fieldnames = list(first_row['Tags'].keys())
            fieldnames.extend(tag_fieldnames)
        
        # Verify all comparison fields exist (either at root level or in Tags)
        missing_fields = []
        for field in comparison_fields:
            if field not in fieldnames:
                missing_fields.append(field)
        
        if missing_fields:
            print(f"Warning: Comparison fields not found in JSON: {missing_fields}")
            print("Available fields:", list(set(fieldnames)))
            return -1
        
        # Filter duplicates
        cleaned_rows = []
        removed_rows = []
        removed_count = 0
        
        for row in original_rows:
            # Create comparison tuple
            comparison_values = []
            for field in comparison_fields:
                # Check if field is in Tags object or at root level
                if 'Tags' in row and isinstance(row['Tags'], dict) and field in row['Tags']:
                    value = row['Tags'].get(field)
                else:
                    value = row.get(field)
                # Convert to string, handling None
                if value is None:
                    value = ""
                else:
                    value = str(value).strip()
                comparison_values.append(value)
            
            comparison_tuple = tuple(comparison_values)
            
            # Check if this row exists in datadog_content.json
            if comparison_tuple in comparison_set:
                removed_count += 1
                removed_rows.append(row)
            else:
                cleaned_rows.append(row)
        
        # Write cleaned JSON back to file
        with open(json_file_path, 'w', encoding='utf-8') as f:
            json.dump(cleaned_rows, f, indent=2, ensure_ascii=False)
        
        # Write removed lines to temp directory
        if removed_rows:
            # Get temp directory from parameter or use default
            temp_dir = temp_directory if temp_directory else "tmp"
            removed_lines_path = Path(temp_dir) / "removed_lines.json"
            removed_lines_path.parent.mkdir(parents=True, exist_ok=True)
            
            try:
                with open(removed_lines_path, 'w', encoding='utf-8') as f:
                    json.dump(removed_rows, f, indent=2, ensure_ascii=False)
                print(f"  → Removed lines saved to: {removed_lines_path}")
            except Exception as e:
                print(f"  Warning: Could not save removed lines to {removed_lines_path}: {e}")
        
        # Display cleaning statistics
        original_count = len(original_rows)
        remaining_count = len(cleaned_rows)
        
        print(f"\nCleaning statistics:")
        print(f"  Original rows: {original_count}")
        print(f"  Removed rows: {removed_count}")
        print(f"  Remaining rows: {remaining_count}")
        
        return removed_count
        
    except Exception as e:
        print(f"Error cleaning JSON file: {e}")
        import traceback
        traceback.print_exc()
        return -1

def ask_user_upload_confirmation(auto_yes=False, item_count=None):
    """Ask user if they want to upload the JSON file to Datadog.
    
    Prompts the user with a yes/no question and returns True if they answer 'y' or 'yes'.
    Displays the number of items to upload before asking for confirmation.
    
    Args:
        auto_yes: If True, automatically return True without prompting
        item_count: Number of items/elements to upload (optional, for display)
        
    Returns:
        True if user wants to upload, False otherwise
    """
    if auto_yes:
        if item_count is not None:
            print(f"\nAuto-confirming upload to Datadog (--yes flag) - {item_count} item(s) to upload")
        else:
            print("\nAuto-confirming upload to Datadog (--yes flag)")
        return True
    
    # Display item count if provided
    if item_count is not None:
        print(f"\nNumber of items to upload: {item_count}")
    
    while True:
        response = input("\nDo you want to upload the JSON file to Datadog? (y/n): ").strip().lower()
        if response in ['y', 'yes']:
            return True
        elif response in ['n', 'no']:
            return False
        else:
            print("Please enter 'y' for yes or 'n' for no.")

def upload_json_to_datadog(datadog_config, json_file_path):
    """Upload a JSON file to Datadog Cloud Cost Management platform.
    
    Reads the JSON file, converts each row to a CustomCostsFileLineItem object,
    and uploads the data using the Datadog API client.
    
    Args:
        datadog_config: Dictionary containing Datadog API credentials
            - api_key: Datadog API key
            - app_key: Datadog Application key
            - site: Datadog site URL (e.g., "datadoghq.eu", "datadoghq.com")
        json_file_path: Path to the JSON file to upload
        
    Returns:
        True if upload was successful, False otherwise
    """
    if datadog_config is None:
        print("Error: Datadog configuration is missing.")
        return False
    
    # Check if datadog_api_client is available
    if not DATADOG_API_AVAILABLE:
        print("Error: datadog_api_client library is not installed.")
        print("Please install it with: pip install datadog-api-client")
        return False
    
    api_key = datadog_config.get("api_key")
    app_key = datadog_config.get("app_key")
    site = datadog_config.get("site", "datadoghq.com")
    
    # Validate that required credentials are present
    if not api_key or not app_key:
        print("Error: Datadog API credentials (api_key, app_key) are missing.")
        return False
    
    json_file_path = Path(json_file_path)
    
    # Check if file exists
    if not json_file_path.exists():
        print(f"Error: JSON file not found: {json_file_path}")
        return False
    
    try:
        # Read JSON file and convert to CustomCostsFileLineItem objects
        body = []
        required_fields = ['ProviderName', 'ChargeDescription', 'ChargePeriodStart', 
                          'ChargePeriodEnd', 'BilledCost', 'BillingCurrency']
        
        with open(json_file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
            if not isinstance(data, list):
                print("Error: JSON file should contain a list of objects.")
                return False
            
            if len(data) == 0:
                print("Error: JSON file is empty.")
                return False
            
            # Get fieldnames from first row
            first_row = data[0]
            if not isinstance(first_row, dict):
                print("Error: JSON file should contain a list of dictionaries.")
                return False
            
            fieldnames = list(first_row.keys())
            
            # Check if all required fields are present
            missing_fields = [f for f in required_fields if f not in fieldnames]
            if missing_fields:
                print(f"Error: Required fields missing in JSON: {missing_fields}")
                print(f"Available fields: {list(fieldnames)}")
                return False
            
            # Convert each row to CustomCostsFileLineItem
            row_count = 0
            for row in data:
                row_count += 1
                
                # Extract required fields
                provider_name = str(row.get('ProviderName', '')).strip()
                charge_description = str(row.get('ChargeDescription', '')).strip()
                charge_period_start = str(row.get('ChargePeriodStart', '')).strip()
                charge_period_end = str(row.get('ChargePeriodEnd', '')).strip()
                
                # Parse billed_cost as float
                try:
                    billed_cost_value = row.get('BilledCost', 0)
                    if isinstance(billed_cost_value, str):
                        billed_cost = float(billed_cost_value.strip() or '0')
                    else:
                        billed_cost = float(billed_cost_value) if billed_cost_value else 0.0
                except (ValueError, TypeError):
                    print(f"Warning: Invalid BilledCost value in row {row_count}: {row.get('BilledCost')}")
                    billed_cost = 0.0
                
                billing_currency = str(row.get('BillingCurrency', '')).strip()
                
                # Extract tags from Tags object (if present)
                tags = {}
                if 'Tags' in row and isinstance(row['Tags'], dict):
                    # Tags are in a dedicated Tags object
                    for tag_key, tag_value in row['Tags'].items():
                        if tag_value is not None and tag_value != "":  # Only include non-empty tags
                            tags[tag_key] = str(tag_value).strip()
                
                # Create CustomCostsFileLineItem object
                try:
                    line_item = CustomCostsFileLineItem(
                        provider_name=provider_name,
                        charge_period_start=charge_period_start,
                        charge_period_end=charge_period_end,
                        charge_description=charge_description,
                        billed_cost=billed_cost,
                        billing_currency=billing_currency,
                        tags=tags if tags else None,  # Only include tags if not empty
                    )
                    body.append(line_item)
                except Exception as e:
                    print(f"Warning: Error creating line item for row {row_count}: {e}")
                    print(f"  Row data: ProviderName={provider_name}, ChargePeriodStart={charge_period_start}")
                    continue
        
        if not body:
            print("Error: No valid rows found in CSV file.")
            return False
        
        print(f"  → Prepared {len(body)} line item(s) for upload")
        
        # Configure Datadog API client
        configuration = Configuration()
        configuration.api_key["apiKeyAuth"] = api_key
        configuration.api_key["appKeyAuth"] = app_key
        configuration.server_variables["site"] = site
        
        # Use JSON format with CustomCostsFileLineItem objects
        print(f"  → Converting JSON to CustomCostsFileLineItem format for upload...")
        
        # Extract filename from JSON file path (without extension)
        json_filename = json_file_path.stem  # Get filename without extension
        
        # Create API client instance and upload
        with ApiClient(configuration) as api_client:
            # Initialize Cloud Cost Management API
            api_instance = cloud_cost_management_api.CloudCostManagementApi(api_client)
            
            try:
                # Upload using the JSON method
                # The Datadog API upload_custom_costs_file method accepts only body parameter
                # However, we can try to pass filename via query parameters using call_with_http_info
                # Check if the endpoint supports query parameters by examining the endpoint definition
                endpoint = api_instance._upload_custom_costs_file_endpoint
                
                # Try to upload with filename as query parameter
                # Note: This may not be supported by the API, but we'll attempt it
                try:
                    # Use call_with_http_info to pass additional parameters
                    response = endpoint.call_with_http_info(
                        body=body,
                        query_params={'filename': json_filename}
                    )
                except (TypeError, AttributeError):
                    # If query_params not supported, try without it
                    try:
                        response = endpoint.call_with_http_info(
                            body=body,
                            header_params={'X-Filename': json_filename}
                        )
                    except (TypeError, AttributeError):
                        # Fallback: upload without filename parameter
                        # The API will use default name 'data', but we've extracted the filename
                        response = api_instance.upload_custom_costs_file(body=body)
                
                print(f"✓ File uploaded successfully to Datadog (JSON format) - filename: {json_filename}")
                
                return True
                
            except Exception as e:
                print(f"Error calling upload_custom_costs_file: {e}")
                import traceback
                traceback.print_exc()
                return False
            
    except Exception as e:
        print(f"Error uploading file to Datadog: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Main function - entry point of the program.
    
    Orchestrates the CSV conversion process:
    1. Loads configuration from .config.json
    2. Lists available CSV files in source files directory (configurable via SourceFilesDirectory in config)
    3. Prompts user to select a file (or uses -file parameter)
    4. Generates output filename with current date
    5. Performs the CSV conversion
    """
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description='Convert and upload cost CSV files to Datadog Cloud Cost Management',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        '-y', '--yes',
        action='store_true',
        help='Automatically answer "yes" to all prompts (use automatic exchange rates, upload to Datadog). Requires -file option.'
    )
    parser.add_argument(
        '-file', '--file',
        type=str,
        help='Name of the CSV file to process (must be in tmp/ directory). Required if -y is used.'
    )
    args = parser.parse_args()
    auto_yes = args.yes
    specified_file = args.file
    
    # Validate: if -y is used, -file must be provided
    if auto_yes and not specified_file:
        parser.error("The -y option requires the -file option to be specified.")
    
    # Step 1: Load configuration file containing field mappings
    config = load_config()
    if config is None:
        return
    
    # Get source files directory from config (default: "cost_file")
    source_files_directory = config.get('SourceFilesDirectory', 'cost_file')
    
    # Step 2: Determine input file path
    date_str = datetime.now().strftime("%Y%m%d")
    
    if specified_file:
        # Use the specified file from source files directory
        input_file_path = Path(source_files_directory) / specified_file
        if not input_file_path.exists():
            print(f"Error: File not found in {source_files_directory}/ directory: {specified_file}")
            return
        
        # Use the specified file name
        selected_file = specified_file
        print(f"\nUsing specified file from {source_files_directory}/: {selected_file}")
    else:
        # Step 2a: List all CSV files available for processing
        csv_files = list_csv_files(source_files_directory)
        if not csv_files:
            return
        
        # Step 2b: Prompt user to select which CSV file to process
        selected_file = select_csv_file(csv_files, auto_yes=auto_yes, directory_name=source_files_directory)
        if selected_file is None:
            return
        
        # Input: from source files directory
        input_file_path = Path(source_files_directory) / selected_file
    
    # Step 3: Build output file path
    # Output: to temp directory with date stamp (format: export_YYYYMMDD.json)
    temp_dir = config.get('TempDirectory', 'tmp')
    output_file_path = Path(temp_dir) / f"{config.get('UploadFilePrefix', 'export_')}{date_str}.json"
    
    # Step 4: Perform the CSV conversion
    print(f"\nProcessing file: {selected_file}")
    print("=" * 60)
    success = convert_csv(input_file_path, output_file_path, config, auto_yes=auto_yes)
    
    # Step 6: Display final result
    if success:
        print("\nConversion completed successfully!")
        
        # Step 7: Download overlapping Datadog cost files after successful conversion
        datadog_config = load_datadog_config()
        if datadog_config:
            temp_dir = config.get('TempDirectory', 'tmp')
            download_overlapping_datadog_files(datadog_config, output_file_path, limit=config.get('DatadogCostFilesLimit', 20), temp_directory=temp_dir)
        
        # Step 8: Clean converted JSON by removing duplicates from datadog_content.json
        comparison_fields = config.get('DeduplicationComparisonFields', 
                                      ["ProviderName", "ChargePeriodStart", "ChargePeriodEnd", "resourceid"])
        temp_dir = config.get('TempDirectory', 'tmp')
        datadog_content_path = Path(temp_dir) / "datadog_content.json"
        
        print("\n" + "=" * 60)
        print("Cleaning converted JSON file...")
        print("=" * 60)
        print(f"Comparison fields: {', '.join(comparison_fields)}")
        
        removed_count = clean_json_file(output_file_path, comparison_fields, datadog_content_path=str(datadog_content_path), temp_directory=temp_dir)
        
        if removed_count >= 0:
            if removed_count > 0:
                print(f"✓ Removed {removed_count} duplicate row(s) from converted JSON.")
            else:
                print("✓ No duplicates found. JSON file is clean.")
        else:
            print("✗ Error during JSON cleaning.")
        
        # Step 9: Ask user if they want to upload the file to Datadog
        if removed_count >= 0:  # Only ask if cleaning was successful
            # Count items in JSON file before asking for confirmation
            item_count = None
            try:
                with open(output_file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        item_count = len(data)
            except Exception as e:
                print(f"Warning: Could not count items in JSON file: {e}")
            
            # Skip upload if no items remain after cleaning
            if item_count is not None and item_count == 0:
                print("\n⚠ No items remaining in cleaned JSON file. Skipping upload.")
                return
            
            if ask_user_upload_confirmation(auto_yes=auto_yes, item_count=item_count):
                print("\n" + "=" * 60)
                print("Uploading JSON file to Datadog...")
                print("=" * 60)
                
                upload_success = upload_json_to_datadog(datadog_config, output_file_path)
                
                if upload_success:
                    print(f"\n✓ File successfully uploaded to Datadog: {output_file_path}")
                    
                    # Step 10: Move uploaded JSON file to output directory
                    output_dir_name = config.get('OutputDirectory', 'output')
                    output_dir = Path(output_dir_name)
                    output_dir.mkdir(parents=True, exist_ok=True)
                    
                    # Move the uploaded JSON to output directory
                    final_output_path = output_dir / output_file_path.name
                    try:
                        shutil.move(str(output_file_path), str(final_output_path))
                        print(f"✓ Moved uploaded file to: {final_output_path}")
                    except Exception as e:
                        print(f"Warning: Could not move file to output directory: {e}")
                    
                    # Step 11: Move removed_lines.json to output directory with date suffix
                    temp_dir = config.get('TempDirectory', 'tmp')
                    removed_lines_source = Path(temp_dir) / "removed_lines.json"
                    if removed_lines_source.exists():
                        removed_lines_output = output_dir / f"removed_lines_{date_str}.json"
                        try:
                            shutil.move(str(removed_lines_source), str(removed_lines_output))
                            print(f"✓ Moved removed lines file to: {removed_lines_output}")
                        except Exception as e:
                            print(f"Warning: Could not move removed_lines.json to output directory: {e}")
                else:
                    print(f"\n✗ Failed to upload file to Datadog: {output_file_path}")
    else:
        print("\nConversion failed.")


if __name__ == "__main__":
    main()

