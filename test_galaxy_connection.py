#!/usr/bin/env python3
"""
Test script to connect to local Galaxy instance using bioblend
"""

from bioblend import galaxy
import json
import os

# Galaxy instance configuration
GALAXY_URL = os.environ.get("GALAXY_URL", "http://127.0.0.1:8080/")
API_KEY = os.environ.get("GALAXY_API_KEY", "")

def test_galaxy_connection():
    """Test connection to Galaxy instance and display basic info"""
    try:
        # Create Galaxy instance connection
        gi = galaxy.GalaxyInstance(url=GALAXY_URL, key=API_KEY)
        
        print(f"Connecting to Galaxy at: {GALAXY_URL}")
        print("="*50)
        
        # Test connection by getting Galaxy version
        version = gi.config.get_version()
        print(f"Galaxy Version: {version}")
        
        # Get current user info
        user_info = gi.users.get_current_user()
        print(f"Connected as user: {user_info['email']}")
        
        # List available tool categories/sections
        print("\nTool Categories:")
        print("-" * 30)
        tools = gi.tools.get_tools()
        categories = set()
        for tool in tools:
            if 'panel_section_name' in tool and tool['panel_section_name']:
                categories.add(tool['panel_section_name'])
        
        for category in sorted(categories):
            print(f"  - {category}")
        
        # Count total tools
        print(f"\nTotal tools available: {len(tools)}")
        
        # List first 5 tools as examples
        print("\nFirst 5 tools:")
        print("-" * 20)
        for i, tool in enumerate(tools[:5]):
            print(f"  {i+1}. {tool['name']} (ID: {tool['id']})")
        
        print("\n✅ Connection successful!")
        return gi
        
    except Exception as e:
        print(f"❌ Error connecting to Galaxy: {e}")
        return None

def list_histories(gi):
    """List available histories"""
    if not gi:
        return
    
    try:
        print("\nHistories:")
        print("-" * 20)
        histories = gi.histories.get_histories()
        for hist in histories:
            print(f"  - {hist['name']} (ID: {hist['id']})")
    except Exception as e:
        print(f"Error getting histories: {e}")

def list_libraries(gi):
    """List available data libraries"""
    if not gi:
        return
    
    try:
        print("\nData Libraries:")
        print("-" * 20)
        libraries = gi.libraries.get_libraries()
        for lib in libraries:
            print(f"  - {lib['name']} (ID: {lib['id']})")
    except Exception as e:
        print(f"Error getting libraries: {e}")

if __name__ == "__main__":
    # Test connection
    gi = test_galaxy_connection()
    
    if gi:
        # Additional info
        list_histories(gi)
        list_libraries(gi)
        
        print("\n" + "="*50)
        print("Bioblend is ready to use with your Galaxy instance!")
        print("You can now use the 'gi' object to interact with Galaxy.")