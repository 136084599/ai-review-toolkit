import json
import threading

import pytest

from app.media import pick_vision_frames, probe, run_ffmpeg, scan_frames, technical_checks
from app.quality import alignment_checks, caption_checks, caption_sources, glossary_checks, normalized, parse_technical
from app.schemas import VideoOptions


def source(text, start=0, end=4, kind="subtitle", id="sub-1", **extra):
    return dict(id=id,kind=kind,text=text,start=start,end=end,**extra)


def test_options_validate_ranges_and_glossary():
    assert VideoOptions(glossary="哈儿滨=>哈尔滨").vision_limit == 48
    for params in [{"vision_limit":10000},{"safe_margin":float("nan")},{"glossary":"坏格式"},
                   {"glossary":"甲=>乙\n甲=>丙"},{"target_lufs":0},{"unknown":True}]:
        with pytest.raises(ValueError):
            VideoOptions(**params)


def test_full_technical_log_including_trailing_freeze_and_stereo():
    log = """lavfi.freezedetect.freeze_start: 1
lavfi.freezedetect.freeze_end: 6
lavfi.freezedetect.freeze_start: 8
lavfi.scd.time: 4.0
lavfi.scd.time: 4.2
[Parsed_astats_1 @ ABC123] Channel: 1
[Parsed_astats_1 @ ABC123] RMS level dB: -10.0
[Parsed_astats_1 @ ABC123] Channel: 2
[Parsed_astats_1 @ ABC123] RMS level dB: -inf
[Parsed_astats_1 @ ABC123] Overall
Integrated loudness:
    I: -14.0 LUFS
Loudness range:
    LRA: 6.2 LU
True peak:
    Peak: 0.2 dBFS
"""
    result=parse_technical(log,{"duration":10,"has_audio":True,"width":1080,"height":1920},VideoOptions(expected_aspect="16:9"))
    titles=[i["title"] for i in result["issues"]]
    assert len(result["scenes"])==3
    assert "画面长时间静止" in titles and "静止画面延续到片尾" in titles
    assert "检测到很短的镜头" in titles and "左右声道音量差异较大" in titles
    assert "整体响度超出自设范围" in titles and "音频真峰值接近满幅" in titles
    assert "画面比例与本次要求不符" in titles
    assert result["metrics"]["channel_rms_dbfs"]==[-10.0,None]
    json.dumps(result,allow_nan=False)


def test_silence_does_not_claim_clipping_or_imbalanced_stereo():
    log="Integrated loudness:\n I: -70.0 LUFS\nTrue peak:\n Peak: -inf dBFS\n"
    result=parse_technical(log,{"duration":10,"has_audio":True},VideoOptions(freeze_check=False,scene_check=False))
    assert result["issues"]==[]
    assert result["metrics"]["true_peak_dbfs"] is None


def test_layout_checks_do_not_treat_top_corner_logo_as_caption():
    frame=source("画面",kind="frame",id="frame-1",boxes=[
        {"text":"台标", "bounds":[.01,.01,.12,.04]},
        {"text":"底部字幕", "bounds":[.01,.97,.5,.985]}])
    findings=caption_checks([frame,frame],[],VideoOptions())
    assert len(findings)==2
    assert {f["evidence"] for f in findings}=={"底部字幕"}
    from app.review import finalize
    assert len(finalize(findings,[],[],[],[])["issues"])==2, "Different layout rules must not be deduplicated away"


def test_reading_speed_and_nested_overlap_use_authored_timings_only():
    subs=[source("新闻内容一",0,10),source("这一行出现得太快了",1,1.3,id="sub-2"),source("第三行",2,3,id="sub-3")]
    issues=caption_checks([],subs,VideoOptions())
    assert sum(i["title"]=="上传字幕时间重叠" for i in issues)==2
    assert any("偏快" in i["title"] for i in issues)
    assert caption_checks([],[],VideoOptions())==[]


def test_caption_durations_stop_on_blank_frame():
    frames=[source("",t,t,kind="frame",id=f"frame-{t}",boxes=([{ "text":"字幕", "bounds":[.1,.8,.5,.9]}] if t!=2 else [])) for t in (0,1,2,3)]
    result=caption_sources(frames,1,4)
    assert len(result)==2
    assert result[0]["end"]==2 and result[1]["start"]==3


@pytest.mark.parametrize("text,expected",[("六十个座位","60个座位"),("二零二六年三月一日","2026年3月1日"),("一万亿人","1000000000000人"),("一方面，统一认识。","一方面统一认识")])
def test_chinese_numeric_normalization_avoids_false_mismatch(text,expected):
    assert normalized(text)==expected


def test_alignment_equivalent_numbers_not_flagged():
    rows,issues=alignment_checks([source("共有六十个阅读座位",kind="speech")],[source("共有60个阅读座位")],authored=True)
    assert rows[0]["status"]=="matched" and issues==[]


def test_alignment_numeric_difference_and_word_time_window():
    speech=[source("今天开放共有60个阅读座位",0,8,kind="speech",words=[
        {"text":"今天开放","start":0,"end":2},{"text":"共有60个阅读座位","start":4,"end":7}])]
    rows,issues=alignment_checks(speech,[source("共有80个阅读座位",4,7)],authored=True)
    assert rows[0]["speech"]=="共有60个阅读座位"
    assert rows[0]["status"]=="different"
    assert issues[0]["category"]=="音字对齐"


def test_alignment_finds_nearby_offset_and_uncovered_speech():
    speech=[source("城市书房正式开放",3,5,kind="speech"),source("这里还有一段新的口播",10,14,kind="speech",id="speech-2")]
    rows,issues=alignment_checks(speech,[source("城市书房正式开放",1,2.4)],authored=True)
    assert rows[0]["status"]=="timing" and rows[0]["timing_offset"]==2
    assert any(i["title"]=="口播片段未被上传字幕覆盖" for i in issues)


def test_ocr_unrelated_title_not_declared_wrong_subtitle():
    rows,issues=alignment_checks([source("今天天气晴朗",kind="speech")],[source("新华社新闻报道",kind="caption")])
    assert issues==[] and rows[0]["status"]=="unmatched"


def test_glossary_matches_only_supplied_wrong_spellings():
    issues=glossary_checks([source("哈儿滨新闻",kind="speech"),source("哈尔滨新闻",id="ok")],"哈儿滨=>哈尔滨")
    assert len(issues)==1 and issues[0]["evidence"]=="哈儿滨"


def test_shot_selection_preserves_diversity_under_budget():
    frames=[source("",i,kind="frame",id=f"frame-{i}",shot=i+1,representative=True,fingerprint=(bytes([i])*576).hex()) for i in range(100)]
    selected=pick_vision_frames(frames,24)
    assert len(selected)==24 and selected[0]["shot"]==1 and selected[-1]["shot"]==100


def test_same_shot_representative_does_not_duplicate_static_frame():
    frames=[source("",i,kind="frame",id=f"frame-{i}",shot=1,representative=i==1,fingerprint=(bytes([30])*576).hex()) for i in range(3)]
    assert [f["id"] for f in pick_vision_frames(frames)]==["frame-1"]


def test_real_media_freeze_scene_audio_and_extra_frame_sampling(tmp_path):
    path=tmp_path/"extended.mp4"
    run_ffmpeg(["-f","lavfi","-i","color=c=red:s=640x360:r=10:d=4", "-f","lavfi","-i","color=c=white:s=640x360:r=10:d=0.2",
        "-f","lavfi","-i","color=c=blue:s=640x360:r=10:d=3", "-f","lavfi","-i","aevalsrc=0.9*sin(2*PI*440*t)|0:s=48000:d=7.2",
        "-filter_complex","[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]","-map","[v]","-map","3:a", "-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac",str(path)],threading.Event(),timeout=30)
    metadata=probe(path)
    result=technical_checks(path,metadata,threading.Event(),VideoOptions(),True)
    assert metadata["channels"]==2 and metadata["sample_rate"]==48000
    assert len(result["scenes"])>=3
    assert any("静止" in i["title"] for i in result["issues"])
    assert any("短的镜头" in i["title"] for i in result["issues"])
    assert any("左右声道" in i["title"] for i in result["issues"])
    assert result["metrics"]["integrated_lufs"] is not None
    frames,_,_=scan_frames(path,tmp_path,metadata,threading.Event(),lambda *a:None,5,result["scenes"])
    assert any(4<=frame["start"]<4.2 for frame in frames), "Short shot should be sampled despite 5s base interval"
