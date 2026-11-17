#!/usr/bin/env python3
"""
Bioblend examples for Galaxy automation
"""

from bioblend import galaxy
import time
import os

# Galaxy configuration
GALAXY_URL = os.environ.get("GALAXY_URL", "http://127.0.0.1:8080/")
API_KEY = os.environ.get("GALAXY_API_KEY", "")

class GalaxyAutomation:
    def __init__(self):
        self.gi = galaxy.GalaxyInstance(url=GALAXY_URL, key=API_KEY)
    
    def create_new_history(self, name="Bioblend Test History"):
        """Create a new history"""
        history = self.gi.histories.create_history(name=name)
        print(f"Created history: {history['name']} (ID: {history['id']})")
        return history
    
    def upload_file_from_path(self, file_path, history_id):
        """Upload a file to Galaxy"""
        try:
            result = self.gi.tools.upload_file(file_path, history_id)
            print(f"Uploaded file: {file_path}")
            return result
        except Exception as e:
            print(f"Error uploading file: {e}")
            return None
    
    def upload_text_content(self, content, filename, history_id):
        """Upload text content as a file"""
        try:
            result = self.gi.tools.paste_content(
                content=content,
                history_id=history_id,
                file_name=filename,
                file_type='txt'
            )
            print(f"Uploaded content as: {filename}")
            return result
        except Exception as e:
            print(f"Error uploading content: {e}")
            return None
    
    def list_tools_by_category(self, category_name):
        """List tools in a specific category"""
        tools = self.gi.tools.get_tools()
        category_tools = [
            tool for tool in tools 
            if tool.get('panel_section_name') == category_name
        ]
        
        print(f"\nTools in '{category_name}' category:")
        print("-" * 40)
        for tool in category_tools:
            print(f"  - {tool['name']} (ID: {tool['id']})")
        
        return category_tools
    
    def run_tool_example(self, tool_id, history_id, inputs):
        """Run a tool with given inputs"""
        try:
            result = self.gi.tools.run_tool(
                history_id=history_id,
                tool_id=tool_id,
                tool_inputs=inputs
            )
            print(f"Started tool execution: {tool_id}")
            return result
        except Exception as e:
            print(f"Error running tool: {e}")
            return None
    
    def monitor_job(self, job_id, timeout=300):
        """Monitor job status until completion"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            job_info = self.gi.jobs.get_job(job_id)
            status = job_info['state']
            print(f"Job {job_id} status: {status}")
            
            if status in ['ok', 'error']:
                return job_info
            
            time.sleep(5)
        
        print(f"Job monitoring timed out after {timeout} seconds")
        return None
    
    def get_dataset_info(self, history_id, dataset_id):
        """Get information about a dataset"""
        try:
            dataset = self.gi.histories.show_dataset(history_id, dataset_id)
            print(f"Dataset: {dataset['name']}")
            print(f"  State: {dataset['state']}")
            print(f"  Size: {dataset.get('file_size', 'Unknown')} bytes")
            print(f"  Type: {dataset.get('file_ext', 'Unknown')}")
            return dataset
        except Exception as e:
            print(f"Error getting dataset info: {e}")
            return None
    
    def download_dataset(self, history_id, dataset_id, output_path):
        """Download a dataset to local file"""
        try:
            self.gi.histories.download_dataset(
                history_id, dataset_id, 
                file_path=output_path, 
                use_default_filename=False
            )
            print(f"Downloaded dataset to: {output_path}")
        except Exception as e:
            print(f"Error downloading dataset: {e}")

def example_workflow():
    """Example workflow using bioblend"""
    print("Starting Galaxy automation example...")
    
    # Initialize automation class
    ga = GalaxyAutomation()
    
    # Create a new history
    history = ga.create_new_history("Bioblend Example Workflow")
    history_id = history['id']
    
    # List tools in a specific category (example: Text Manipulation)
    ga.list_tools_by_category("Text Manipulation")
    
    print(f"\nExample workflow completed!")
    print(f"History ID: {history_id}")
    print("You can view this history in your Galaxy interface.")

if __name__ == "__main__":
    example_workflow()