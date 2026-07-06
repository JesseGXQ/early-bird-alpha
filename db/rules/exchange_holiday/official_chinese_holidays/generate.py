import os
import datetime
import csv
import chinese_calendar

# Define mappings
holiday_name_map = {
    "New Year's Day": "元旦",
    "Spring Festival": "春节",
    "Tomb-sweeping Day": "清明节",
    "Labour Day": "劳动节",
    "Dragon Boat Festival": "端午节",
    "National Day": "国庆节",
    "Mid-autumn Festival": "中秋节",
    "Anti-Fascist 70th Day": "中国人民抗日战争暨世界反法西斯战争胜利70周年纪念日",
}

def generate_holidays():
    start_date = datetime.date(2013, 1, 1)
    end_date = datetime.date(2026, 12, 31)
    
    records = []
    curr = start_date
    while curr <= end_date:
        if curr in chinese_calendar.holidays:
            eng_name = chinese_calendar.get_holiday_detail(curr)[1]
            chn_name = holiday_name_map.get(eng_name)
            if not chn_name:
                raise ValueError(f"Unknown holiday: {eng_name} on {curr}")
            records.append((curr.strftime("%Y-%m-%d"), chn_name))
        curr += datetime.timedelta(days=1)
        
    print(f"Generated {len(records)} holiday days.")
    
    # Write to csv (relative to script path)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = os.path.join(script_dir, "holiday.csv")
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "holiday_str"])
        writer.writerows(records)
    print(f"Successfully wrote to {output_path}")

if __name__ == "__main__":
    generate_holidays()
