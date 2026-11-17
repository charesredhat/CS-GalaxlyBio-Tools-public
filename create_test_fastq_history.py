#!/usr/bin/env python3
"""
Create a test history with FASTQ data for testing the microrunQC framework
"""

from bioblend import galaxy
import time
import os

# Galaxy configuration
GALAXY_URL = os.environ.get("GALAXY_URL", "http://127.0.0.1:8080/")
API_KEY = os.environ.get("GALAXY_API_KEY", "")

gi = galaxy.GalaxyInstance(url=GALAXY_URL, key=API_KEY)

# Create test FASTQ data (forward and reverse reads)
test_fastq_r1 = """@SEQ_1/1
GATTTGGGGTTCAAAGCAGTATCGATCAAATAGTAAATCCATTTGTTCAACTCACAGTTT
+
!''*((((***+))%%%++)(%%%%).1***-+*''))**55CCF>>>>>>CCCCCCC65
@SEQ_2/1
GATTTGGGGTTCAAAGCAGTATCGATCAAATAGTAAATCCATTTGTTCAACTCACAGTTT
+
!''*((((***+))%%%++)(%%%%).1***-+*''))**55CCF>>>>>>CCCCCCC65
@SEQ_3/1
ACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGT
+
IIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIII
"""

test_fastq_r2 = """@SEQ_1/2
AAACTGTGAGTTGAACAAATGGATTTACTATTTATCGATACTGCTTTGAACCCCAAATC
+
!''*((((***+))%%%++)(%%%%).1***-+*''))**55CCF>>>>>>CCCCCCC65
@SEQ_2/2
AAACTGTGAGTTGAACAAATGGATTTACTATTTATCGATACTGCTTTGAACCCCAAATC
+
!''*((((***+))%%%++)(%%%%).1***-+*''))**55CCF>>>>>>CCCCCCC65
@SEQ_3/2
ACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGT
+
IIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIII
"""

def create_test_history():
    """Create a new history with FASTQ test data"""
    print("🧬 Creating test history with FASTQ data...")
    
    # Create new history
    history_name = f"MicrorunQC FASTQ Test - {int(time.time())}"
    history = gi.histories.create_history(name=history_name)
    history_id = history['id']
    
    print(f"📂 Created history: {history_name}")
    print(f"🆔 History ID: {history_id}")
    
    try:
        # Upload forward reads
        print("📤 Uploading forward reads (R1)...")
        upload_r1 = gi.tools.paste_content(
            content=test_fastq_r1,
            history_id=history_id,
            file_type='fastqsanger'
        )
        
        if upload_r1 and 'outputs' in upload_r1:
            dataset_r1_id = upload_r1['outputs'][0]['id']
            print(f"✅ Forward reads uploaded: {dataset_r1_id}")
            
            # Rename the dataset
            gi.histories.update_dataset(history_id, dataset_r1_id, name="test_sample_R1.fastq")
        
        # Upload reverse reads  
        print("📤 Uploading reverse reads (R2)...")
        upload_r2 = gi.tools.paste_content(
            content=test_fastq_r2,
            history_id=history_id,
            file_type='fastqsanger'
        )
        
        if upload_r2 and 'outputs' in upload_r2:
            dataset_r2_id = upload_r2['outputs'][0]['id']
            print(f"✅ Reverse reads uploaded: {dataset_r2_id}")
            
            # Rename the dataset
            gi.histories.update_dataset(history_id, dataset_r2_id, name="test_sample_R2.fastq")
        
        # Wait a moment for processing
        print("⏳ Waiting for uploads to process...")
        time.sleep(10)
        
        # Check the uploaded datasets
        datasets = gi.histories.show_history(history_id, contents=True)
        print(f"\n📊 History now contains {len(datasets)} datasets:")
        
        for dataset in datasets:
            print(f"   📄 {dataset['name']}")
            print(f"      Type: {dataset.get('file_ext', 'unknown')}")
            print(f"      State: {dataset.get('state', 'unknown')}")
            print(f"      Size: {dataset.get('file_size', 0)} bytes")
            print()
        
        print(f"✅ Test history ready!")
        print(f"🚀 You can now test with:")
        print(f"   python complete_microrunqc_test.py --history-id {history_id}")
        print(f"   python complete_microrunqc_test.py --history-name \"{history_name}\"")
        
        return history_id, history_name
        
    except Exception as e:
        print(f"❌ Error uploading data: {e}")
        return None, None

if __name__ == "__main__":
    create_test_history()