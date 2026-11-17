import csv
import xml.etree.ElementTree as ET
from typing import List, Tuple, Dict

def read_csv_column(csv_file_path: str, column_index: int = 0) -> Dict[str, List[int]]:
    """
    Read CSV file and extract values from specified column with their row positions.
    
    Args:
        csv_file_path: Path to the CSV file
        column_index: Index of the column to read (0-based, default is 0 for Column A)
        
    Returns:
        Dictionary mapping values to list of row positions
    """
    column_values = {}
    
    try:
        with open(csv_file_path, 'r', newline='', encoding='utf-8') as file:
            csv_reader = csv.reader(file)
            
            # Skip header if present
            header = next(csv_reader, None)
            print(f"DEBUG: CSV Header: {header}")
            
            for row_num, row in enumerate(csv_reader, start=2):  # Start at 2 (accounting for header)
                if row and len(row) > column_index:  # Check if row has enough columns
                    value = row[column_index].strip()  # Use specified column index
                    if value:  # Only add non-empty values
                        if value not in column_values:
                            column_values[value] = []
                        column_values[value].append(row_num)
                        
    except FileNotFoundError:
        print(f"Error: CSV file '{csv_file_path}' not found.")
        return {}
    except Exception as e:
        print(f"Error reading CSV file: {e}")
        return {}
    
    # Debug: Show first 10 unique CSV values
    print(f"DEBUG: First 10 CSV values from column {column_index + 1}:")
    for i, (value, rows) in enumerate(list(column_values.items())[:10]):
        print(f"  '{value}' (found in rows: {rows})")
    
    return column_values

def read_xml_values(xml_file_path: str) -> Dict[str, List[str]]:
    """
    Read XML file and extract all text values with their element paths.
    
    Args:
        xml_file_path: Path to the XML file
        
    Returns:
        Dictionary mapping values to list of element paths
    """
    xml_values = {}
    
    try:
        tree = ET.parse(xml_file_path)
        root = tree.getroot()
        print(f"DEBUG: XML Root element: {root.tag}")
        
        def extract_values(element, path=""):
            current_path = f"{path}/{element.tag}" if path else element.tag
            
            # Add element text if it exists and is not just whitespace
            if element.text and element.text.strip():
                value = element.text.strip()
                if value not in xml_values:
                    xml_values[value] = []
                xml_values[value].append(current_path)
            
            # Add attribute values
            for attr_name, attr_value in element.attrib.items():
                if attr_value.strip():
                    attr_path = f"{current_path}@{attr_name}"
                    if attr_value not in xml_values:
                        xml_values[attr_value] = []
                    xml_values[attr_value].append(attr_path)
            
            # Recursively process child elements
            for child in element:
                extract_values(child, current_path)
        
        extract_values(root)
        
    except FileNotFoundError:
        print(f"Error: XML file '{xml_file_path}' not found.")
        return {}
    except ET.ParseError as e:
        print(f"Error parsing XML file: {e}")
        return {}
    except Exception as e:
        print(f"Error reading XML file: {e}")
        return {}
    
    # Debug: Show first 10 unique XML values
    print(f"DEBUG: First 10 XML values:")
    for i, (value, paths) in enumerate(list(xml_values.items())[:10]):
        print(f"  '{value}' (found at: {', '.join(paths)})")
    
    return xml_values

def find_matches(csv_values: Dict[str, List[int]], xml_values: Dict[str, List[str]]) -> List[Tuple[str, int, str]]:
    """
    Find matching values between CSV Column and XML file.

    xml_values is already a flat dict keyed by the XML leaf value (see
    read_xml_values), so checking whether a CSV value occurs anywhere
    in the XML is a direct dict membership test -- no need to walk the
    original nested XML structure again per CSV value.

    Args:
        csv_values: Dictionary of CSV column values and their row positions
        xml_values: Dictionary of XML values and their element paths

    Returns:
        List of tuples containing (matching_value, csv_row, xml_path)
    """
    matches = []

    print("DEBUG: Looking for matches...")
    match_count = 0

    for csv_value in csv_values:
        if csv_value in xml_values:
            for csv_row in csv_values[csv_value]:
                for xml_path in xml_values[csv_value]:
                    matches.append((csv_value, csv_row, xml_path))
                    match_count += 1
                    if match_count <= 5:  # Show first 5 matches as debug
                        print(f"DEBUG: Match found - '{csv_value}' at CSV row {csv_row} and XML path {xml_path}")

    return matches

def create_results_file(matches: List[Tuple[str, int, str]], output_file: str):
    """
    Create a results file showing all matches.
    
    Args:
        matches: List of match tuples
        output_file: Path for the output file
    """
    try:
        with open(output_file, 'w', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            
            # Write header
            writer.writerow(['Matching Value', 'Source Row (CSV File)', 'Target Location (XML File)'])
            
            # Write matches
            for match in matches:
                writer.writerow([match[0], match[1], match[2]])
                
        print(f"Results saved to: {output_file}")
        
    except Exception as e:
        print(f"Error creating results file: {e}")

def get_column_index() -> int:
    """
    Get column index from user input with validation.
    
    Returns:
        Valid column index (0-based)
    """
    while True:
        try:
            column_input = input("Enter the column number to match (1 for A, 2 for B, etc.): ").strip()
            column_number = int(column_input)
            
            if column_number < 1:
                print("Column number must be 1 or greater. Please try again.")
                continue
                
            return column_number - 1  # Convert to 0-based index
            
        except ValueError:
            print("Please enter a valid number.")
        except KeyboardInterrupt:
            print("\nOperation cancelled.")
            exit()

def main():
    """
    Main function to orchestrate the file comparison process.
    """
    # File paths - modify these according to your file locations
    csv_file_path = input("Enter the path to the CSV file: ").strip()
    xml_file_path = input("Enter the path to the XML file: ").strip()
    column_index = get_column_index()
    
    print("\n" + "="*50)
    print("COMPARING FILES")
    print("="*50)
    
    # Read CSV column values
    print(f"Reading CSV file (Column {column_index + 1})...")
    csv_values = read_csv_column(csv_file_path, column_index)
    print(f"Found {len(csv_values)} unique values in CSV Column {column_index + 1}")
    
    # Read XML values
    print("Reading XML file...")
    xml_values = read_xml_values(xml_file_path)
    print(f"Found {len(xml_values)} unique values in XML file")
    
    # Find matches
    print("Finding matches...")
    matches = find_matches(csv_values, xml_values)
    
    # Display results
    print(f"\nFound {len(matches)} matches:")
    print("-" * 80)
    print(f"{'Matching Value':<20} {'CSV Row':<10} {'XML Location':<50}")
    print("-" * 80)
    
    for match in matches[:10]:  # Show first 10 matches
        print(f"{match[0]:<20} {match[1]:<10} {match[2]:<50}")
    
    if len(matches) > 10:
        print(f"... and {len(matches) - 10} more matches")
    
    # Create results file
    if matches:
        output_file_path = "found-mtch.txt"
        create_results_file(matches, output_file_path)
    else:
        print("No matches found. No results file created.")
    
    print(f"\nComparison complete!")

if __name__ == "__main__":
    main()
