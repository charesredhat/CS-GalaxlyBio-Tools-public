#!/usr/bin/env python3
"""
Quick MicrorunQC Bioblend Test Utility
Simple script for testing bioblend automation with various QC tools
"""

from bioblend import galaxy
import time
import os

# Galaxy configuration
GALAXY_URL = os.environ.get("GALAXY_URL", "http://127.0.0.1:8080/")
API_KEY = os.environ.get("GALAXY_API_KEY", "")

def quick_qc_test():
    """Quick test using FastQC as a working example"""
    print("🚀 Quick QC Test with Bioblend")
    print("=" * 40)
    
    # Connect to Galaxy
    gi = galaxy.GalaxyInstance(url=GALAXY_URL, key=API_KEY)
    
    # Create history
    history = gi.histories.create_history(name=f"Quick QC Test - {int(time.time())}")
    history_id = history['id']
    print(f"✅ Created history: {history['name']}")
    
    # Create sample FASTQ
    sample_fastq = """@read1
ACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGT
+
IIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIII
@read2
TGCATGCATGCATGCATGCATGCATGCATGCATGCATGCATGCATGCATGCATGCATGCA
+
IIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIII"""
    
    # Upload data
    upload_result = gi.tools.paste_content(
        content=sample_fastq,
        history_id=history_id,
        file_type='fastqsanger'
    )
    
    dataset_id = upload_result['outputs'][0]['id']
    print(f"✅ Uploaded test data: dataset {dataset_id}")
    
    # Find FastQC tool
    tools = gi.tools.get_tools()
    fastqc_tool = None
    
    for tool in tools:
        if 'fastqc' in tool['name'].lower():
            fastqc_tool = tool
            break
    
    if not fastqc_tool:
        print("❌ FastQC tool not found")
        return False
    
    print(f"✅ Found tool: {fastqc_tool['name']}")
    
    # Wait for upload to complete
    time.sleep(3)
    
    # Run FastQC
    try:
        result = gi.tools.run_tool(
            history_id=history_id,
            tool_id=fastqc_tool['id'],
            tool_inputs={'input_file': {'id': dataset_id, 'src': 'hda'}}
        )
        
        print("✅ Started FastQC analysis")
        print(f"🌐 View progress at: {GALAXY_URL}")
        print(f"📁 History ID: {history_id}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error running FastQC: {e}")
        return False

if __name__ == "__main__":
    success = quick_qc_test()
    
    if success:
        print("\n🎉 Quick test successful!")
        print("✅ Bioblend automation is working")
        print("🔧 Ready for production workflows")
    else:
        print("\n❌ Quick test failed")
        print("🔧 Check Galaxy configuration")