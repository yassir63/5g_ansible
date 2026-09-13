#!/usr/bin/env python3
"""
CSI Data Filter - RB range filtering and RB-level aggregation
Supports: RB range selection, RB-level aggregation
"""

import csv
import sys
import argparse
from pathlib import Path

class CSIFilter:
    def __init__(self, input_csv, output_csv, config):
        self.input_csv = Path(input_csv)
        self.output_csv = Path(output_csv)
        self.config = config
    
    def should_keep_record(self, record):
        """Check if record matches RB range config"""
        rb = int(record['rb'])
        
        if self.config['rb_selection'] == 'range':
            if not (self.config['rb_start'] <= rb <= self.config['rb_end']):
                return False
        
        return True
    
    def aggregate_by_rb(self, records):
        """Aggregate subcarrier data to RB level (average 12 SC per RB)"""
        rb_data = {}
        
        for rec in records:
            frame = rec['frame']
            slot = rec['slot']
            rb = int(rec['rb'])
            key = (frame, slot, rb)
            
            if key not in rb_data:
                rb_data[key] = {
                    'frame': frame,
                    'slot': slot,
                    'rb': rb,
                    'real_sum': 0,
                    'imag_sum': 0,
                    'count': 0
                }
            
            rb_data[key]['real_sum'] += int(rec['real'])
            rb_data[key]['imag_sum'] += int(rec['imag'])
            rb_data[key]['count'] += 1
        
        # Average over subcarriers
        result = []
        for key in sorted(rb_data.keys()):
            data = rb_data[key]
            if data['count'] > 0:
                avg_real = int(round(data['real_sum'] / data['count']))
                avg_imag = int(round(data['imag_sum'] / data['count']))
                result.append({
                    'frame': data['frame'],
                    'slot': data['slot'],
                    'rb': data['rb'],
                    'real': avg_real,
                    'imag': avg_imag
                })
        
        return result
    
    def filter(self):
        """Apply filter and write output"""
        print(f"[CSIFilter] Reading {self.input_csv}")
        
        try:
            with open(self.input_csv, 'r') as f:
                reader = csv.DictReader(f)
                
                # Validate required columns
                required_fields = ['frame', 'slot', 'rb', 'real', 'imag']
                if reader.fieldnames is None:
                    raise ValueError("CSV is empty or invalid")
                
                missing = [f for f in required_fields if f not in reader.fieldnames]
                if missing:
                    raise ValueError(f"Missing required columns: {missing}")
                
                # Read and filter records
                records = []
                for row_num, row in enumerate(reader, start=2):
                    try:
                        if self.should_keep_record(row):
                            records.append(row)
                    except (KeyError, ValueError) as e:
                        print(f"WARNING: Skipping row {row_num}: {e}")
                        continue
        
        except IOError as e:
            print(f"ERROR: Cannot read input file: {e}")
            sys.exit(1)
        except ValueError as e:
            print(f"ERROR: {e}")
            sys.exit(1)
        
        if not records:
            print("[CSIFilter] WARNING: No records matched filter criteria")
        else:
            print(f"[CSIFilter] Filtered to {len(records)} records")
        
        # Aggregate to RB level if needed
        if self.config['level'] == 'rb':
            records = self.aggregate_by_rb(records)
            print(f"[CSIFilter] Aggregated to {len(records)} RB records")
        
        # Write output
        try:
            with open(self.output_csv, 'w', newline='') as f:
                fieldnames = ['frame', 'slot', 'rb', 'real', 'imag']
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(records)
            
            print(f"[CSIFilter] Wrote {len(records)} records to {self.output_csv}")
        
        except IOError as e:
            print(f"ERROR: Cannot write output file: {e}")
            sys.exit(1)

def main():
    parser = argparse.ArgumentParser(
        description='CSI CSV Filter - RB range selection and aggregation',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  csi_filter.py --input raw.csv --output filtered.csv --level rb
  csi_filter.py --input raw.csv --output filtered.csv --level rb --rb-selection range --rb-start 0 --rb-end 50
        """)
    
    parser.add_argument('--input', required=True, help='Input CSV file')
    parser.add_argument('--output', required=True, help='Output CSV file')
    parser.add_argument('--level', choices=['subcarrier', 'rb'], default='rb',
                       help='Aggregation level (default: rb)')
    parser.add_argument('--rb-selection', choices=['all', 'range'], default='all',
                       help='RB selection mode (default: all)')
    parser.add_argument('--rb-start', type=int, default=0,
                       help='RB range start (default: 0)')
    parser.add_argument('--rb-end', type=int, default=105,
                       help='RB range end (default: 105)')
    
    args = parser.parse_args()
    
    config = {
        'level': args.level,
        'rb_selection': args.rb_selection,
        'rb_start': args.rb_start,
        'rb_end': args.rb_end
    }
    
    filt = CSIFilter(args.input, args.output, config)
    filt.filter()

if __name__ == '__main__':
    main()
