"""Small live editorial evaluation on explicitly fictional text, no private newsroom data."""
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import ROOT, Settings
from app.review import DeepSeek

settings=Settings()
client=DeepSeek(settings.key,settings.model)
cases=[
    {"name":"试点被扩大为全国", "title":"补贴惠及全国所有居民", "text":"本月开始，这项补贴面向全国所有居民，无需申请即可领取。",
     "reference":"虚构测试文件：仅在甲县、乙县开展试点。符合条件的申请人须提交申请并经审核。", "dimension":"policy"},
    {"name":"计划被说成已完成", "title":"今年十座书房全部建成", "text":"截至本月，十座城市书房已全部建成开放。",
     "reference":"虚构采访确认记录：今年计划建设十座城市书房，目前两座开放，其余八座尚在建设。", "dimension":"facts"},
    {"name":"辟谣引述不应当成作者主张", "title":"两县开展补贴试点，网传全国发放不实", "text":"网传消息称全国所有居民都可领取补贴。记者核实后发现，该说法不实。政策仅在甲县和乙县试点，符合条件者需申请，审核后领取。",
     "reference":"虚构测试文件：该补贴仅在甲县、乙县开展试点，符合条件者须申请并审核。全国发放的传言已被明确否认。", "dimension":None},
]
results=[]
for case in cases:
    sources=[{"id":"video-title","kind":"title","text":case["title"]},
             {"id":"speech-1","kind":"speech","text":case["text"],"start":0,"end":10}]
    result=client.content(sources,case["reference"],"明确为虚构测试案例，按证据审核，避免对引用后已否认的说法误报。",threading.Event(),"central","global")
    if case["dimension"]:
        passed=any(i["basis"]=="reference_conflict" for i in result["issues"])
    else:
        passed=not any(i["basis"] in ("internal_conflict","reference_conflict") for i in result["issues"])
    passed=passed and not result["warnings"]
    results.append({"name":case["name"],"passed":passed,**result})
    print(json.dumps({"case":case["name"],"passed":passed,"issues":len(result["issues"]),"warnings":result["warnings"]},ensure_ascii=True),flush=True)
(ROOT/"test-results/content-live.json").write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf-8")
assert all(result["passed"] for result in results), "Live editorial evaluation failed; inspect content-live.json"
