#!/usr/bin/env python3
"""
Bioblend automation script for testing microrunQC tool with FASTQ datasets
"""

from bioblend import galaxy
import time
import os
import sys
import json

# Galaxy configuration
GALAXY_URL = os.environ.get("GALAXY_URL", "http://127.0.0.1:8080/")
API_KEY = os.environ.get("GALAXY_API_KEY", "")

class MicrorunQCTester:
    def __init__(self):
        self.gi = galaxy.GalaxyInstance(url=GALAXY_URL, key=API_KEY)
        self.history_id = None
        
    def find_microrunqc_tool(self):
        """Find the microrunQC tool in Galaxy"""
        print("Searching for microrunQC tool...")
        tools = self.gi.tools.get_tools()
        
        microrunqc_tools = []
        for tool in tools:
            if 'microrun' in tool['name'].lower() or 'microrun' in tool['id'].lower():
                microrunqc_tools.append(tool)
                print(f"Found: {tool['name']} (ID: {tool['id']})")
        
        if not microrunqc_tools:
            print("❌ No microrunQC tool found in Galaxy instance")
            print("Available QC tools:")
            qc_tools = [tool for tool in tools if 'qc' in tool['name'].lower()]
            for tool in qc_tools[:10]:  # Show first 10 QC tools
                print(f"  - {tool['name']} (ID: {tool['id']})")
            return None
        
        return microrunqc_tools[0]  # Return first match
    
    def create_test_history(self):
        """Create a new history for testing"""
        history_name = f"MicrorunQC Test - {int(time.time())}"
        history = self.gi.histories.create_history(name=history_name)
        self.history_id = history['id']
        print(f"✅ Created test history: {history_name} (ID: {self.history_id})")
        return history
    
    def upload_test_fastq_data(self):
        """Upload test FASTQ data or use existing datasets"""
        print("Looking for existing FASTQ datasets...")

        # Ask Galaxy's dataset index for FASTQ-typed datasets directly instead
        # of iterating every history and fetching its full contents: O(1)
        # API calls per page instead of 1 (list histories) + N (show_history
        # per history), and it also stops swallowing every per-history error
        # silently. Paginate explicitly since get_datasets() defaults to the
        # 500 most recent datasets instance-wide and would otherwise silently
        # miss older FASTQ data on a long-lived, heavily-used instance.
        history_names = {h['id']: h['name'] for h in self.gi.histories.get_histories()}
        datasets = []
        page_size = 500
        offset = 0
        while True:
            page = self.gi.datasets.get_datasets(
                extension=['fastq', 'fastqsanger', 'fastq.gz'],
                limit=page_size,
                offset=offset
            )
            datasets.extend(page)
            if len(page) < page_size:
                break
            offset += page_size

        fastq_datasets = [
            {
                'name': dataset['name'],
                'id': dataset['id'],
                'history_id': dataset.get('history_id'),
                'history_name': history_names.get(dataset.get('history_id'), 'Unknown'),
                'file_ext': dataset.get('extension'),
                'state': dataset.get('state')
            }
            for dataset in datasets
        ]

        if fastq_datasets:
            print(f"Found {len(fastq_datasets)} existing FASTQ datasets:")
            for i, dataset in enumerate(fastq_datasets[:5]):  # Show first 5
                print(f"  {i+1}. {dataset['name']} ({dataset['file_ext']}) in {dataset['history_name']}")
            return fastq_datasets
        
        print("No existing FASTQ datasets found. Creating sample data...")
        return self.create_sample_fastq_data()
    
    def create_sample_fastq_data(self):
        """Create sample FASTQ data for testing"""
        sample_fastq_r1 = """@SEQ_ID_1/1
GATTTGGGGTTCAAAGCAGTATCGATCAAATAGTAAATCCATTTGTTCAACTCACAGTTT
+
!''*((((***+))%%%++)(%%%%).1***-+*''))**55CCF>>>>>>CCCCCCC65
@SEQ_ID_2/1
GATTTGGGGTTCAAAGCAGTATCGATCAAATAGTAAATCCATTTGTTCAACTCACAGTTT
+
!''*((((***+))%%%++)(%%%%).1***-+*''))**55CCF>>>>>>CCCCCCC65
@SEQ_ID_3/1
GATTTGGGGTTCAAAGCAGTATCGATCAAATAGTAAATCCATTTGTTCAACTCACAGTTT
+
!''*((((***+))%%%++)(%%%%).1***-+*''))**55CCF>>>>>>CCCCCCC65"""

        sample_fastq_r2 = """@SEQ_ID_1/2
TTCAAAGCAGTATCGATCAAATAGTAAATCCATTTGTTCAACTCACAGTTTGATTTGGGG
+
CCF>>>>>>CCCCCCC65!''*((((***+))%%%++)(%%%%).1***-+*''))**55
@SEQ_ID_2/2
TTCAAAGCAGTATCGATCAAATAGTAAATCCATTTGTTCAACTCACAGTTTGATTTGGGG
+
CCF>>>>>>CCCCCCC65!''*((((***+))%%%++)(%%%%).1***-+*''))**55
@SEQ_ID_3/2
TTCAAAGCAGTATCGATCAAATAGTAAATCCATTTGTTCAACTCACAGTTTGATTTGGGG
+
CCF>>>>>>CCCCCCC65!''*((((***+))%%%++)(%%%%).1***-+*''))**55"""

        try:
            # Upload R1 reads
            r1_result = self.gi.tools.paste_content(
                content=sample_fastq_r1,
                history_id=self.history_id,
                file_type='fastqsanger'
            )
            
            # Upload R2 reads
            r2_result = self.gi.tools.paste_content(
                content=sample_fastq_r2,
                history_id=self.history_id,
                file_type='fastqsanger'
            )
            
            print("✅ Created sample FASTQ datasets (R1 and R2)")
            return [
                {'name': 'Sample_R1.fastq', 'id': r1_result['outputs'][0]['id'], 'type': 'R1'},
                {'name': 'Sample_R2.fastq', 'id': r2_result['outputs'][0]['id'], 'type': 'R2'}
            ]
            
        except Exception as e:
            print(f"❌ Error creating sample data: {e}")
            return []
    
    def copy_dataset_to_history(self, dataset_id, source_history_id):
        """Copy a dataset from another history to current test history"""
        try:
            result = self.gi.histories.copy_dataset(
                history_id=self.history_id,
                dataset_id=dataset_id,
                source=source_history_id
            )
            print(f"✅ Copied dataset to test history")
            return result
        except Exception as e:
            print(f"❌ Error copying dataset: {e}")
            return None
    
    def run_microrunqc_analysis(self, tool_info, fastq_datasets):
        """Run microrunQC analysis on FASTQ datasets"""
        print(f"Running microrunQC tool: {tool_info['name']}")
        
        # Get tool details to understand input parameters
        try:
            tool_details = self.gi.tools.show_tool(tool_info['id'])
            print("Tool inputs available:")
            for input_param in tool_details.get('inputs', []):
                print(f"  - {input_param.get('name', 'unknown')}: {input_param.get('type', 'unknown')}")
        except Exception as e:
            print(f"Could not get tool details: {e}")
        
        # Prepare inputs for microrunQC
        # This will vary based on the actual tool requirements
        tool_inputs = {}
        
        # Try to identify input parameters
        if fastq_datasets:
            # Assume first dataset is the input
            tool_inputs = {
                'input': {'id': fastq_datasets[0]['id'], 'src': 'hda'}
            }
            
            # If there are paired reads, add them
            if len(fastq_datasets) > 1:
                tool_inputs['input2'] = {'id': fastq_datasets[1]['id'], 'src': 'hda'}
        
        try:
            result = self.gi.tools.run_tool(
                history_id=self.history_id,
                tool_id=tool_info['id'],
                tool_inputs=tool_inputs
            )
            print(f"✅ Started microrunQC analysis")
            return result
        except Exception as e:
            print(f"❌ Error running microrunQC: {e}")
            print("This might be due to incorrect input parameters.")
            print("Tool may require specific input format or additional parameters.")
            return None
    
    def monitor_analysis(self, job_result, timeout=600):
        """Monitor the analysis job until completion"""
        if not job_result:
            return None
            
        print("Monitoring analysis progress...")
        start_time = time.time()
        
        # Get job IDs from the result
        job_ids = []
        for output in job_result.get('outputs', []):
            if 'creating_job' in output:
                job_ids.append(output['creating_job'])
        
        if not job_ids:
            print("No job IDs found in result")
            return None
        
        while time.time() - start_time < timeout:
            all_completed = True
            job_states = []
            
            for job_id in job_ids:
                try:
                    job_info = self.gi.jobs.show_job(job_id)
                    state = job_info['state']
                    job_states.append(state)
                    
                    if state not in ['ok', 'error']:
                        all_completed = False
                except Exception as e:
                    print(f"Error checking job {job_id}: {e}")
                    continue
            
            print(f"Job states: {', '.join(job_states)}")
            
            if all_completed:
                print("✅ All jobs completed!")
                return job_states
            
            if 'error' in job_states:
                print("❌ One or more jobs failed")
                # Get error details
                for job_id in job_ids:
                    try:
                        job_info = self.gi.jobs.show_job(job_id)
                        if job_info['state'] == 'error':
                            print(f"Job {job_id} error details:")
                            print(f"  Tool: {job_info.get('tool_id', 'unknown')}")
                            print(f"  Message: {job_info.get('stderr', 'No error message')}")
                    except Exception:
                        # Not a bare `except:` -- that also catches
                        # KeyboardInterrupt, which made this 10s poll loop
                        # impossible to Ctrl-C out of.
                        continue
                return job_states
            
            time.sleep(10)  # Wait 10 seconds before checking again
        
        print(f"❌ Analysis timed out after {timeout} seconds")
        return job_states
    
    def analyze_results(self):
        """Analyze the results from microrunQC"""
        print("Analyzing results...")
        
        try:
            datasets = self.gi.histories.show_history(self.history_id, contents=True)
            output_datasets = []
            
            for dataset in datasets:
                if dataset.get('state') == 'ok' and not dataset.get('deleted'):
                    output_datasets.append({
                        'name': dataset['name'],
                        'id': dataset['id'],
                        'file_ext': dataset.get('file_ext'),
                        'file_size': dataset.get('file_size', 0)
                    })
            
            print(f"Generated {len(output_datasets)} output datasets:")
            for dataset in output_datasets:
                size_mb = dataset['file_size'] / (1024*1024) if dataset['file_size'] else 0
                print(f"  - {dataset['name']} ({dataset['file_ext']}) - {size_mb:.2f} MB")
            
            return output_datasets
            
        except Exception as e:
            print(f"❌ Error analyzing results: {e}")
            return []
    
    def run_complete_test(self):
        """Run the complete microrunQC test workflow"""
        print("=" * 60)
        print("Starting MicrorunQC Bioblend Automation Test")
        print("=" * 60)
        
        # Step 1: Find microrunQC tool
        tool_info = self.find_microrunqc_tool()
        if not tool_info:
            return False
        
        # Step 2: Create test history
        self.create_test_history()
        
        # Step 3: Get or create FASTQ data
        fastq_datasets = self.upload_test_fastq_data()
        if not fastq_datasets:
            print("❌ No FASTQ datasets available for testing")
            return False
        
        # If using existing datasets, copy them to test history
        if 'history_id' in fastq_datasets[0]:
            copied_datasets = []
            for dataset in fastq_datasets[:2]:  # Use first 2 datasets
                if dataset['state'] == 'ok':
                    copied = self.copy_dataset_to_history(dataset['id'], dataset['history_id'])
                    if copied:
                        copied_datasets.append({'id': copied['id'], 'name': dataset['name']})
            fastq_datasets = copied_datasets
        
        if not fastq_datasets:
            print("❌ No usable FASTQ datasets")
            return False
        
        # Step 4: Run microrunQC analysis
        job_result = self.run_microrunqc_analysis(tool_info, fastq_datasets)
        
        # Step 5: Monitor analysis
        if job_result:
            job_states = self.monitor_analysis(job_result)
        
        # Step 6: Analyze results
        results = self.analyze_results()
        
        print("=" * 60)
        print("MicrorunQC Test Summary")
        print("=" * 60)
        print(f"Tool used: {tool_info['name']}")
        print(f"History ID: {self.history_id}")
        print(f"Input datasets: {len(fastq_datasets)}")
        print(f"Output datasets: {len(results) if results else 0}")
        print(f"Test completed: {'✅ Success' if results else '❌ Failed'}")
        
        return True

def main():
    """Main function to run the microrunQC test"""
    tester = MicrorunQCTester()
    success = tester.run_complete_test()
    
    if success:
        print("\n🎉 MicrorunQC automation test completed!")
        print(f"Visit your Galaxy instance to view results: {GALAXY_URL}")
    else:
        print("\n❌ MicrorunQC automation test failed!")
        sys.exit(1)

if __name__ == "__main__":
    main()