# Datadog Cloud Cost Management Upload Tool

Tool to convert and upload cost CSV files to Datadog Cloud Cost Management (CCM) platform.

## ⚠️ Important Warning

**This tool is a proof of concept (POC) and should NOT be used in production environments.**

- This is **not an official Datadog tool** - it was developed independently
- This tool is provided **as-is** and can be used as a **source of inspiration** for developing production-ready scripts
- I **cannot be held responsible** for any issues, data loss, or problems that may arise from using this tool
- Use at your own risk and always test thoroughly in non-production environments before considering any production use
- For production needs, please develop your own solution or consult with Datadog support for official tools and best practices

## Features

- **CSV to JSON Conversion**: Converts CSV files to JSON format with customizable field mappings
- **Currency Detection & Conversion**: Automatically detects non-USD currencies and converts them to USD using real-time or fixed exchange rates
- **Deduplication**: Removes duplicate entries by comparing with existing Datadog cost files
- **Datadog Integration**: Downloads overlapping cost files from Datadog and uploads converted files
- **Automated Mode**: Supports `-y` flag for non-interactive execution
- **File Selection**: Supports `-file` parameter to specify which file to process
- **Configurable Directories**: All directories (source, temp, output) are configurable via `.config.json`

## Requirements

- Python 3.6+
- `datadog-api-client` library (use pip to install it)

Install dependencies:
```bash
pip install datadog-api-client
```

## Configuration

### 1. Datadog Configuration (`.datadog.json`)

Create a `.datadog.json` file in the project root with your Datadog API credentials:

```json
{
    "api_key": "your_api_key",
    "app_key": "your_app_key",
    "site": "datadoghq.eu"
}
```

**Note**: Copy `.datadog.json.sample` to `.datadog.json` and fill in your credentials.

### 2. Field Mapping Configuration (`.config.json`)

The `.config.json` file defines how source CSV columns are mapped to target columns:

- **`SourceFilesDirectory`**: Directory where source CSV files are located (default: `cost_file`)
- **`TempDirectory`**: Directory for temporary files during processing (default: `tmp`)
- **`OutputDirectory`**: Directory where uploaded files are moved after successful upload (default: `output`)
- **`UploadFilePrefix`**: Prefix for output JSON files (default: `dd_ccm_upload_`)
- **`DatadogCostFilesLimit`**: Maximum number of Datadog files to fetch for deduplication
- **`DeduplicationComparisonFields`**: Fields used to identify duplicate rows
- **`currency`**: Dictionary mapping currency codes to exchange rates or `"dynamic"`
  - If value is a number: use that fixed exchange rate to USD
  - If value is `"dynamic"`: fetch exchange rate from internet
  - If currency not in config: fetch from internet (default behavior)
- **`MandatoryFieldsTargetSourceMappingß`**: Maps mandatory target fields to source CSV columns
- **`TagsFieldsTargetSourceMapping`**: Maps tag fields to source CSV columns (stored in a dedicated "Tags" object in JSON output)

Example configuration:
```json
{
    "SourceFilesDirectory": "cost_file",
    "TempDirectory": "tmp",
    "OutputDirectory": "sent",
    "UploadFilePrefix": "dd_ccm_upload_",
    "DatadogCostFilesLimit": 30,
    "DeduplicationComparisonFields": ["ProviderName", "ChargePeriodStart", "ChargePeriodEnd", "resourceid"],
    "currency": {
        "EUR": 1.085000,
        "GBP": "dynamic",
        "JPY": 0.006750
    },
    "MandatoryFieldsTargetSourceMappingß": {
        "ProviderName": "ProviderName",
        "ChargeDescription": "ChargeDescription",
        "ChargePeriodStart": "BillingPeriodStart",
        "ChargePeriodEnd": "BillingPeriodEnd",
        "BilledCost": "BilledCost",
        "BillingCurrency": "BillingCurrency"
    },
    "TagsFieldsTargetSourceMapping": {
        "resourceid": "ResourceId",
        "regionid": "RegionId",
        ...
    }
}
```

**Note**: Copy `.config.json.sample` to `.config.json` and fill in your data.

## Usage

### Basic Usage

1. Place your CSV files in the source files directory (default: `cost_file/`, configurable via `SourceFilesDirectory` in `.config.json`)
2. Run the script:
```bash
python upload_cost_file.py
```

3. The script will:
   - List available CSV files
   - Ask you to select a file
   - Detect currencies and prompt for conversion rates (if needed)
   - Convert the CSV file to JSON format
   - Download overlapping Datadog cost files (displays file name and charge period for each)
   - Remove duplicates
   - Display the number of items to upload
   - Ask if you want to upload to Datadog (skipped if no items remain after cleaning)

### Specifying a File

Use the `-file` parameter to specify which file to process:

```bash
python upload_cost_file.py -file export-sample.csv
```

The file must be in the source files directory (default: `cost_file/`, configurable via `SourceFilesDirectory`).

### Automated Mode (Non-Interactive), for scheduled usage

Use the `-y` or `--yes` flag to automatically answer "yes" to all prompts. **Note**: The `-file` parameter is required when using `-y`:

```bash
python upload_cost_file.py -y -file export-sample.csv
```

This will:
- Use the specified file (no file selection prompt)
- Use automatic exchange rates for currency conversion
- Automatically upload to Datadog after conversion

## Workflow

1. **File Selection**: Choose a CSV file from the source files directory (or specify with `-file`)
2. **Currency Detection**: Detects non-USD currencies in the CSV
3. **Exchange Rate Conversion**: 
   - Fetches real-time exchange rates from the internet
   - Prompts user to confirm or enter manual rates (or uses automatic rates with `-y`)
   - Converts all costs to USD
4. **CSV to JSON Conversion**: 
   - Maps source columns to target columns based on `.config.json`
   - Formats dates to YYYY-MM-DD
   - Groups tags into a dedicated "Tags" object
   - Saves to `{TempDirectory}/dd_ccm_upload_YYYYMMDD.json`
5. **Datadog File Download**: 
   - Finds Datadog cost files with overlapping charge periods
   - Displays file name and charge period for each file being extracted
   - Downloads and combines them into `{TempDirectory}/datadog_content.json`
6. **Deduplication**: 
   - Compares converted JSON with Datadog content
   - Removes duplicate rows based on comparison fields
   - Saves removed lines to `{TempDirectory}/removed_lines.json`
   - Displays cleaning statistics (original, removed, remaining counts)
7. **Upload to Datadog**: 
   - Displays the number of items to upload
   - Prompts user for confirmation (or automatically uploads with `-y`)
   - Skips upload prompt if no items remain after cleaning
   - Uploads the cleaned JSON file to Datadog CCM using the JSON filename
8. **File Organization**: 
   - After successful upload, moves the uploaded JSON to `{OutputDirectory}/` directory
   - Moves `removed_lines.json` to `{OutputDirectory}/removed_lines_YYYYMMDD.json` with date suffix

## Output Files

### During Processing (in `{TempDirectory}/` directory, default: `tmp/`)
- `{TempDirectory}/dd_ccm_upload_YYYYMMDD.json`: Converted and cleaned JSON file
- `{TempDirectory}/datadog_content.json`: Combined content from overlapping Datadog files
- `{TempDirectory}/removed_lines.json`: Lines removed during deduplication

### After Upload (in `{OutputDirectory}/` directory, default: `output/`)
- `{OutputDirectory}/dd_ccm_upload_YYYYMMDD.json`: Uploaded JSON file (moved from `{TempDirectory}/`)
- `{OutputDirectory}/removed_lines_YYYYMMDD.json`: Removed lines file with date suffix (moved from `{TempDirectory}/`)

**Note**: Directory names are configurable via `.config.json` (`TempDirectory` and `OutputDirectory`).

## Command Line Options

- `-y`, `--yes`: Automatically answer "yes" to all prompts (non-interactive mode). **Requires `-file` option.**
- `-file`, `--file`: Specify the name of the CSV file to process (must be in the source files directory)

## Notes

- Date formats are automatically converted to YYYY-MM-DD
- Currency conversion uses real-time exchange rates from `exchangerate-api.com` or fixed rates from configuration
- The script handles `UnparsedObject` types from Datadog API responses
- Tag fields from the source CSV are stored in a dedicated "Tags" object in the JSON output
- All directories are configurable via `.config.json`:
  - `SourceFilesDirectory`: Where source CSV files are located (default: `cost_file`)
  - `TempDirectory`: Where temporary files are stored during processing (default: `tmp`)
  - `OutputDirectory`: Where uploaded files are moved after successful upload (default: `output`)
- Files are automatically moved to the configured output directory after successful upload to Datadog
- The script displays file names and charge periods when extracting Datadog files
- Upload is skipped automatically if no items remain after deduplication
- The JSON filename is used when uploading to Datadog (instead of default "data")
