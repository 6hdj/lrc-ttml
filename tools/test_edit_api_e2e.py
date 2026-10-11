"""端到端测 P1 编辑台的三个端点（对着真跑起来的服务打）。

用法：
    # 1) 起服务（另开一个窗口或后台任务）
    $env:L2T_PORT="8791"; .venv\\Scripts\\python.exe web_server.py
    # 2) 打端点
    .venv\\Scripts\\python.exe tools\\test_edit_api_e2e.py

覆盖：validate 只算不写 / 结构化错误契约 / 保存闭环 + 乐观锁 409 /
      .edit.json 落盘与恢复 / 损坏降级 / 真 AMLL 解析器复检。
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("L2T_BASE", "http://127.0.0.1:8791")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 仓库根
sys.path.insert(0, HERE)

import edit_api  # noqa: E402

ok = True


def call(method, path, body=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw[:200]}


def check(name, cond, detail=""):
    global ok
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f"   {detail}"))
    ok = ok and cond


SAMPLE = os.path.join(HERE, "outputs", "在出发之前.duet.ttml")
if not os.path.exists(SAMPLE):
    print(f"缺少样例文件 {SAMPLE}（先跑一次转换生成）")
    sys.exit(2)
ttml = open(SAMPLE, encoding="utf-8").read()

print("[1] POST /api/validate —— 用 TTML 文本实时校验")
st, r = call("POST", "/api/validate", {"ttml": ttml})
check("状态 200", st == 200, str(st))
check("返回 normalize + check + doc", all(k in r for k in ("normalize", "check", "doc")),
      str(list(r))[:120])
check("正常文件 0 error", r["check"]["ok"] and not r["check"]["errors"], str(r["check"])[:120])
check("正常文件不需要修复", r["normalize"]["fixes"] == [], str(r["normalize"]["summary"]))
check("带上了统计", r.get("stats", {}).get("lines") == 32, str(r.get("stats")))

print("\n[2] GET /api/edit —— 没有内容的 id 要是结构化 404")
st, g = call("GET", "/api/edit?id=__no_such_task__")
check("404 + hint", st == 404 and "hint" in g, f"{st} {str(g)[:140]}")

print("\n[3] POST /api/validate —— 拖坏一个字，必须自动修复，且**不改调用方**")
doc_bad = edit_api.doc_from_ttml(ttml)
line = doc_bad.lines[3]
line.words[1].begin = line.words[0].begin - 0.5
snapshot = line.words[1].begin
st, r2 = call("POST", "/api/validate", {"doc": doc_bad.to_dict()})
check("坏时间被自动修复", len(r2["normalize"]["fixes"]) >= 1, r2["normalize"]["summary"])
check("修复摘要可读", "自动修复" in r2["normalize"]["summary"], r2["normalize"]["summary"])
check("只算不写（调用方的 doc 没被改）", line.words[1].begin == snapshot,
      f"变成 {line.words[1].begin}")
fixed = r2["doc"]["lines"][3]["words"][1]["begin"]
check("返回体带修好的 doc 供前端刷新", fixed != snapshot, f"仍是 {fixed}")

print("\n[4] POST /api/validate —— 空 doc / 无 lines 的 doc 都要结构化错误")
for body, label in (({"doc": {}}, "空 doc"), ({"doc": {"version": 1}}, "没有 lines 的 doc")):
    st, e = call("POST", "/api/validate", body)
    check(f"{label} → 400 + hint", st == 400 and "hint" in e, f"{st} {str(e)[:120]}")

print("\n[5] 错误契约：四个字段齐全")
for body, label in (({}, "空 body"), ({"doc": {"nope": 1}}, "doc 结构错"),
                    ({"ttml": "<tt></tt>"}, "空 TTML")):
    st, e = call("POST", "/api/validate", body)
    check(f"{label} → 有 error/hint", st >= 400 and "error" in e and "hint" in e,
          f"{st} {e}")

print("\n[6] POST /api/edit —— 完整保存闭环（乐观锁 + 落盘 + TTML 同步）")
doc6 = edit_api.doc_from_ttml(ttml)
st, s1 = call("POST", "/api/edit", {"id": "e2e_save", "version": 0,
                                    "doc": doc6.to_dict()})
check("首次保存 200", st == 200, f"{st} {str(s1)[:120]}")
check("版本推进到 1", s1.get("version") == 1, str(s1.get("version")))
check("返回了 TTML", (s1.get("ttml") or "").startswith("<?xml"), (s1.get("ttml") or "")[:40])
check(".edit.json 落盘", os.path.exists(os.path.join(HERE, "outputs/e2e_save.edit.json")))

st, s2 = call("POST", "/api/edit", {"id": "e2e_save", "version": 0, "doc": doc6.to_dict()})
check("旧版本号再存 → 409", st == 409, f"{st} {str(s2)[:110]}")
check("409 也是结构化错误", "hint" in s2 and "error" in s2, str(s2)[:110])

st, s3 = call("POST", "/api/edit", {"id": "e2e_save", "version": 1, "doc": s1["doc"]})
check("正确版本号可以继续保存", st == 200 and s3.get("version") == 2,
      f"{st} v={s3.get('version')}")

st, rv = call("POST", "/api/validate", {"ttml": s3["ttml"]})
check("保存后的 TTML 通过严格校验", rv["check"]["ok"] and not rv["check"]["errors"],
      str(rv["check"])[:140])
check("保存后仍是 32 行 266 字",
      rv["stats"]["lines"] == 32 and rv["stats"]["words"] == 266, str(rv["stats"]))

print("\n[7] 保存后的 TTML 交给真 AMLL 解析器复检")
sys.path.insert(0, os.path.join(HERE, "tools"))
try:
    from verify_amll_parse import parse_check
    pr = parse_check(s3["ttml"], tag="_e2e_saved.ttml")
except Exception as e:
    pr = None
    print(f"  (导入失败 {type(e).__name__})")
if pr is None:
    print("  (没有 node / 解析器未准备，跳过)")
else:
    check("真解析器：保存后依然可显示", pr.get("ok"), str(pr))
    check("真解析器：逐字没丢（243 字）", pr.get("words") == 243, str(pr.get("words")))

print("\n[8] GET /api/edit —— 从磁盘 .edit.json 恢复（模拟重启后继续编辑）")
st, g8 = call("GET", "/api/edit?id=e2e_save")
check("能从磁盘恢复", st == 200 and g8["source"] == "disk", f"{st} {g8.get('source')}")
check("恢复后版本是 2", g8["version"] == 2, str(g8.get("version")))
check("恢复后行数一致", g8["stats"]["lines"] == 32, str(g8.get("stats")))

print("\n[9] 落盘文件损坏时必须降级，不能 500")
bad_path = os.path.join(HERE, "outputs/e2e_save.edit.json")
with open(bad_path, "w", encoding="utf-8") as f:
    f.write("{ 这不是合法 JSON")
st, g9 = call("GET", "/api/edit?id=e2e_save")
check("损坏的 .edit.json 降级（不 500）", st in (200, 404), f"{st} {str(g9)[:120]}")

print("\n[10] 清理")
for p in ("outputs/e2e_save.edit.json", "outputs/e2e_save.ttml"):
    fp = os.path.join(HERE, p)
    if os.path.exists(fp):
        os.remove(fp)
check("临时文件已清理", not os.path.exists(bad_path))

print("\n" + ("全部端点自测通过 ✓" if ok else "有失败项 ✗"))
sys.exit(0 if ok else 1)
