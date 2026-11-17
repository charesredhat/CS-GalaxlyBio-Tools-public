#!/usr/bin/env python3
"""
Enhanced MicrorunQC Bioblend Test - with proper parameter handling
"""

from bioblend import galaxy
import time
import json
import os

# Galaxy configuration
GALAXY_URL = os.environ.get("GALAXY_URL", "http://127.0.0.1:8080/")
API_KEY = os.environ.get("GALAXY_API_KEY", "")

class EnhancedMicrorunQCTester:
    def __init__(self):
        self.gi = galaxy.GalaxyInstance(url=GALAXY_URL, key=API_KEY)
        self.history_id = None
        self.microrunqc_tool_id = "toolshed.g2.bx.psu.edu/repos/estrain/microrunqc/microrunqc/1.0.2"
        
    def get_tool_details(self):
        """Get detailed information about the microrunQC tool"""
        print("Getting microrunQC tool details...")
        try:
            tool_details = self.gi.tools.show_tool(self.microrunqc_tool_id)
            
            print("Tool Information:")
            print(f"  Name: {tool_details.get('name', 'Unknown')}")
            print(f"  Version: {tool_details.get('version', 'Unknown')}")
            print(f"  Description: {tool_details.get('description', 'No description')}")
            
            print("\nRequired Inputs:")
            for input_param in tool_details.get('inputs', []):
                param_name = input_param.get('name', 'unknown')
                param_type = input_param.get('type', 'unknown')
                required = not input_param.get('optional', True)
                
                print(f"  - {param_name} ({param_type}) {'[REQUIRED]' if required else '[OPTIONAL]'}")
                
                # Show options for select parameters
                if input_param.get('type') == 'select':
                    options = input_param.get('options', [])
                    if options:
                        print(f"    Options: {[opt[1] for opt in options[:5]]}")  # Show first 5 options
            
            return tool_details
            
        except Exception as e:
            print(f"❌ Error getting tool details: {e}")
            return None
    
    def get_data_managers_and_databases(self):
        """Check for available databases and data managers"""
        print("Checking for available databases...")
        
        try:
            # Check for data managers
            data_managers = self.gi.tool_data.get_data_managers()
            print(f"Found {len(data_managers)} data managers")
            
            # Look for MLST-related data tables
            data_tables = self.gi.tool_data.get_data_tables()
            mlst_tables = [table for table in data_tables if 'mlst' in table.lower()]
            
            if mlst_tables:
                print(f"MLST-related data tables: {mlst_tables}")
            else:
                print("No MLST data tables found - may need to install databases")
            
            return data_tables
            
        except Exception as e:
            print(f"Error checking databases: {e}")
            return []
    
    def create_test_history(self):
        """Create a new history for testing"""
        history_name = f"Enhanced MicrorunQC Test - {int(time.time())}"
        history = self.gi.histories.create_history(name=history_name)
        self.history_id = history['id']
        print(f"✅ Created test history: {history_name}")
        return history
    
    def upload_real_fastq_samples(self):
        """Create more realistic FASTQ samples for testing"""
        # Larger, more realistic FASTQ samples
        sample_fastq_r1 = """@M00967:43:000000000-A3JHG:1:1101:18327:1699 1:N:0:TAGATCGC
TACGGAGGATGCGAGCGTTGTCCGGAATCACTGGGCGTAAAGCGCGCGTAGGCGGTTTTTTAAGTCTGATGTGAAAGCCCACGGCTCAACCGTGGAGGGTCATTGGAAACTGGGGAACTTGAGTGCAGAAGAGGAAAGTGGAATTCCAAGTGTAGCGGTGAAATGCGCAGAGATTTGGAGGAACACCAGTGGCGAAGGCGACTTTCTGGTCTGTAACTGACGCTGATGTGCGAAAGCGTGGGGATCAAACAGG
+
BBBBBFFFFFFFGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGG
@M00967:43:000000000-A3JHG:1:1101:14069:1827 1:N:0:TAGATCGC
TACGGAGGATGCGAGCGTTGTCCGGAATCACTGGGCGTAAAGCGCGCGTAGGCGGTTTTTTAAGTCTGATGTGAAAGCCCACGGCTCAACCGTGGAGGGTCATTGGAAACTGGGGAACTTGAGTGCAGAAGAGGAAAGTGGAATTCCAAGTGTAGCGGTGAAATGCGCAGAGATTTGGAGGAACACCAGTGGCGAAGGCGACTTTCTGGTCTGTAACTGACGCTGATGTGCGAAAGCGTGGGGATCAAACAGG
+
BBBBBFFFFFFFGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGG
@M00967:43:000000000-A3JHG:1:1101:15284:1854 1:N:0:TAGATCGC
TACGGAGGATGCGAGCGTTGTCCGGAATCACTGGGCGTAAAGCGCGCGTAGGCGGTTTTTTAAGTCTGATGTGAAAGCCCACGGCTCAACCGTGGAGGGTCATTGGAAACTGGGGAACTTGAGTGCAGAAGAGGAAAGTGGAATTCCAAGTGTAGCGGTGAAATGCGCAGAGATTTGGAGGAACACCAGTGGCGAAGGCGACTTTCTGGTCTGTAACTGACGCTGATGTGCGAAAGCGTGGGGATCAAACAGG
+
BBBBBFFFFFFFGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGG"""

        sample_fastq_r2 = """@M00967:43:000000000-A3JHG:1:1101:18327:1699 2:N:0:TAGATCGC
CCTGTTTGATCCCCACGCTTTCGCACATCAGCGTCAGTTACAGACCAGAAAGTCGCCTTCGCCACTGGTGTTCCTCCAAATCTCTGCGCATTTCACCGCTACACTTGGAATTCCACTTTCCTCTTCTGCACTCAAGTTCCCCAGTTTCCAATGACCCTCCACGGTTGAGCCGTGGGCTTTCACATCAGACTTAAAAAACCGCCTACGCGCGCTTTACGCCCAGTGATTCCGGACAACGCTCGCATCCTCCGTA
+
@CCFFFFFHHHHHJIJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJ
@M00967:43:000000000-A3JHG:1:1101:14069:1827 2:N:0:TAGATCGC
CCTGTTTGATCCCCACGCTTTCGCACATCAGCGTCAGTTACAGACCAGAAAGTCGCCTTCGCCACTGGTGTTCCTCCAAATCTCTGCGCATTTCACCGCTACACTTGGAATTCCACTTTCCTCTTCTGCACTCAAGTTCCCCAGTTTCCAATGACCCTCCACGGTTGAGCCGTGGGCTTTCACATCAGACTTAAAAAACCGCCTACGCGCGCTTTACGCCCAGTGATTCCGGACAACGCTCGCATCCTCCGTA
+
@CCFFFFFHHHHHJIJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJ
@M00967:43:000000000-A3JHG:1:1101:15284:1854 2:N:0:TAGATCGC
CCTGTTTGATCCCCACGCTTTCGCACATCAGCGTCAGTTACAGACCAGAAAGTCGCCTTCGCCACTGGTGTTCCTCCAAATCTCTGCGCATTTCACCGCTACACTTGGAATTCCACTTTCCTCTTCTGCACTCAAGTTCCCCAGTTTCCAATGACCCTCCACGGTTGAGCCGTGGGCTTTCACATCAGACTTAAAAAACCGCCTACGCGCGCTTTACGCCCAGTGATTCCGGACAACGCTCGCATCCTCCGTA
+
@CCFFFFFHHHHHJIJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJ"""

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
            
            print("✅ Created realistic FASTQ samples")
            
            # Wait for uploads to complete
            time.sleep(5)
            
            return [
                {'name': 'Test_Sample_R1.fastq', 'id': r1_result['outputs'][0]['id']},
                {'name': 'Test_Sample_R2.fastq', 'id': r2_result['outputs'][0]['id']}
            ]
            
        except Exception as e:
            print(f"❌ Error creating FASTQ samples: {e}")
            return []
    
    def run_microrunqc_with_minimal_params(self, fastq_datasets):
        """Run microrunQC with minimal required parameters"""
        print("Running microrunQC with minimal parameters...")
        
        if len(fastq_datasets) < 2:
            print("❌ Need paired FASTQ files")
            return None
        
        # Minimal inputs for microrunQC
        tool_inputs = {
            'forward_reads': {
                'id': fastq_datasets[0]['id'],
                'src': 'hda'
            },
            'reverse_reads': {
                'id': fastq_datasets[1]['id'], 
                'src': 'hda'
            },
            # Skip optional MLST database parameter
            'mlst_databases': '',  # Empty string or None
            'sample_name': 'Test_Sample'
        }
        
        try:
            print("Attempting to run with inputs:")
            for key, value in tool_inputs.items():
                if isinstance(value, dict):
                    print(f"  {key}: dataset {value.get('id', 'unknown')}")
                else:
                    print(f"  {key}: {value}")
            
            result = self.gi.tools.run_tool(
                history_id=self.history_id,
                tool_id=self.microrunqc_tool_id,
                tool_inputs=tool_inputs
            )
            
            print("✅ Successfully submitted microrunQC job")
            return result
            
        except Exception as e:
            print(f"❌ Error running microrunQC: {e}")
            
            # Try alternative approach - without MLST parameter
            print("Trying without MLST parameter...")
            tool_inputs_alt = {
                'forward_reads': {
                    'id': fastq_datasets[0]['id'],
                    'src': 'hda'
                },
                'reverse_reads': {
                    'id': fastq_datasets[1]['id'],
                    'src': 'hda'
                },
                'sample_name': 'Test_Sample'
            }
            
            try:
                result = self.gi.tools.run_tool(
                    history_id=self.history_id,
                    tool_id=self.microrunqc_tool_id,
                    tool_inputs=tool_inputs_alt
                )
                print("✅ Successfully submitted with alternative inputs")
                return result
            except Exception as e2:
                print(f"❌ Alternative approach also failed: {e2}")
                return None
    
    def monitor_and_report(self, job_result):
        """Monitor job and report results"""
        if not job_result:
            return False
        
        print("Monitoring microrunQC execution...")
        
        # Extract job IDs
        job_ids = []
        for output in job_result.get('outputs', []):
            if 'creating_job' in output:
                job_ids.append(output['creating_job'])
        
        print(f"Monitoring {len(job_ids)} jobs...")
        
        # Monitor for up to 10 minutes
        timeout = 600
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            all_done = True
            current_states = []
            
            for job_id in job_ids:
                try:
                    job_info = self.gi.jobs.show_job(job_id)
                    state = job_info['state']
                    current_states.append(state)
                    
                    if state not in ['ok', 'error']:
                        all_done = False
                    
                except Exception as e:
                    print(f"Error checking job {job_id}: {e}")
                    current_states.append('unknown')
            
            print(f"Job states: {', '.join(current_states)}")
            
            if all_done:
                success_count = current_states.count('ok')
                error_count = current_states.count('error')
                
                print(f"✅ All jobs completed: {success_count} successful, {error_count} failed")
                
                # Show final results
                self.show_final_results()
                return success_count > 0
            
            time.sleep(15)  # Check every 15 seconds
        
        print("❌ Jobs timed out")
        return False
    
    def show_final_results(self):
        """Show the final results of the analysis"""
        print("\nFinal Results:")
        print("-" * 40)
        
        try:
            datasets = self.gi.histories.show_history(self.history_id, contents=True)
            
            outputs = []
            for dataset in datasets:
                if (dataset.get('state') == 'ok' and 
                    dataset.get('hid', 0) > 2 and  # Skip input files
                    not dataset.get('deleted', False)):
                    outputs.append(dataset)
            
            if outputs:
                print(f"Generated {len(outputs)} output files:")
                for dataset in outputs:
                    size = dataset.get('file_size', 0)
                    size_str = f"{size/1024:.1f} KB" if size else "Unknown size"
                    print(f"  📄 {dataset['name']} ({dataset.get('file_ext', 'unknown')}) - {size_str}")
            else:
                print("No output files generated")
                
        except Exception as e:
            print(f"Error retrieving results: {e}")
    
    def run_full_test(self):
        """Run the complete enhanced test"""
        print("=" * 70)
        print("Enhanced MicrorunQC Bioblend Automation Test")
        print("=" * 70)
        
        # Step 1: Get tool details
        tool_details = self.get_tool_details()
        if not tool_details:
            return False
        
        # Step 2: Check databases  
        self.get_data_managers_and_databases()
        
        # Step 3: Create test history
        self.create_test_history()
        
        # Step 4: Upload test data
        fastq_datasets = self.upload_real_fastq_samples()
        if not fastq_datasets:
            print("❌ Failed to create test data")
            return False
        
        # Step 5: Run microrunQC
        job_result = self.run_microrunqc_with_minimal_params(fastq_datasets)
        
        # Step 6: Monitor and report
        if job_result:
            success = self.monitor_and_report(job_result)
        else:
            success = False
        
        print("\n" + "=" * 70)
        print("Test Summary")
        print("=" * 70)
        print(f"History: {self.history_id}")
        print(f"Result: {'✅ SUCCESS' if success else '❌ FAILED'}")
        print(f"Galaxy URL: {GALAXY_URL}")
        
        return success

def main():
    tester = EnhancedMicrorunQCTester()
    success = tester.run_full_test()
    
    if not success:
        print("\n💡 Troubleshooting Tips:")
        print("1. Check if microrunQC tool is properly installed")
        print("2. Verify MLST databases are available")
        print("3. Check Galaxy logs for detailed error messages")
        print("4. Ensure input FASTQ files are in correct format")

if __name__ == "__main__":
    main()