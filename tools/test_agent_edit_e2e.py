"""端到端测「手动改声部」：改 agent → 保存 → 检查 TTML 与校验

关键回归点（以前是真 bug）：
  * 保存**不能**删掉 x-duet-a/b 角色（那是 Apple Music 左右分栏依赖的东西）
  * 改完的 agent 要真的写进 <p>，新 agent（v3）要自动出现在 head 声明里
  * 角色要跟着 agent 走，不能出现 ttm:agent="v1" + ttm:role="x-duet-b" 自相矛盾
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("L2T_BASE", "http://127.0.0.1:8793")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))

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
        return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")


def check(name, cond, detail=""):
    global ok
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f"   {detail}"))
    ok = ok and cond


SRC = os.path.join(HERE, "outputs", "在出发之前.duet.ttml")
ttml = open(SRC, encoding="utf-8").read()
doc = edit_api.doc_from_ttml(ttml)
before = {l.agent for l in doc.lines}
print(f"[1] 载入 {SRC}\n  行数 {len(doc.lines)}，agent {sorted(before)}，"
      f"角色 {sorted({l.role for l in doc.lines if l.role})}")

print("\n[2] 改两行为 v2（模拟人工精修），另加一行 v3")
doc.lines[0].agent = "v2"
doc.lines[0].role = "x-duet-b"
doc.lines[1].agent = "v2"
doc.lines[1].role = "x-duet-b"
doc.lines[2].agent = "v3"
doc.lines[2].role = ""

st, r = call("POST", "/api/edit", {"id": "e2e_agent", "version": 0, "doc": doc.to_dict()})
check("保存 200", st == 200, f"{st} {str(r)[:140]}")
out = r.get("ttml") or ""

print("\n[3] 保存后的 TTML 检查")
for a in ("v1", "v2", "v3"):
    n = len(re.findall(r'<p [^>]*ttm:agent="%s"' % a, out))
    print(f"     <p> 里 {a} 的行数 {n}")
check("v3 出现在 head 声明里", 'xml:id="v3"' in out, out[:200])
check("x-duet-a 角色还在", 'ttm:role="x-duet-a"' in out)
check("x-duet-b 角色还在", 'ttm:role="x-duet-b"' in out)
check("角色跟着 agent 走（没有 agent=v1 + role=x-duet-b 的矛盾）",
      not re.search(r'ttm:agent="v1"[^>]*ttm:role="x-duet-b"', out)
      and not re.search(r'ttm:role="x-duet-b"[^>]*ttm:agent="v1"', out))
check("x-bg 和声层没被动", 'ttm:role="x-bg"' in out)
check("行数没变", len(re.findall(r"<p ", out)) == len(doc.lines),
      str(len(re.findall(r"<p ", out))))
check("格式校验通过", r["check"]["ok"], str(r["check"]["errors"][:2]))

print("\n[4] 真 AMLL 解析器复检（逐字不能丢）")
try:
    from verify_amll_parse import parse_check
    pr = parse_check(out, tag="_e2e_agent.ttml")
except Exception as e:
    pr = None
    print(f"  (导入失败 {type(e).__name__})")
if pr is None:
    print("  (未准备解析器，跳过)")
else:
    check("真解析器：可显示", pr.get("ok"), str(pr))
    check("真解析器：逐字完整", pr.get("words") == 243, str(pr.get("words")))
    check("真解析器：agent 分布 3 种", len(pr.get("byAgent", {})) == 3,
          str(pr.get("byAgent")))

print("\n[5] 保存后再载入，改动还在（编辑态落盘）")
st, g = call("GET", "/api/edit?id=e2e_agent")
check("能从磁盘恢复", st == 200 and g["source"] == "disk", f"{st} {g.get('source')}")
check("恢复后 v3 还在", any(l["agent"] == "v3" for l in g["doc"]["lines"]))
check("恢复后行 1 是 v2", g["doc"]["lines"][0]["agent"] == "v2",
      g["doc"]["lines"][0]["agent"])

print("\n[6] 往返保真：只改 agent，不该动别的东西")
d2 = edit_api.doc_from_ttml(r["ttml"])
check("再解析再写回仍然一致",
      edit_api.doc_to_ttml(d2) == r["ttml"])
check("write_options 记录了源文件的写法",
      bool(d2.write_options) and d2.write_options.get("markers") in ("both", "role"),
      str(d2.write_options))

for p in ("outputs/e2e_agent.edit.json", "outputs/e2e_agent.ttml"):
    fp = os.path.join(HERE, p)
    if os.path.exists(fp):
        os.remove(fp)

print("\n" + ("声部手动精修 ✓" if ok else "有失败项 ✗"))
sys.exit(0 if ok else 1)
