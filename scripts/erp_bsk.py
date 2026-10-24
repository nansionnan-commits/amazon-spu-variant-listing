#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ERP 数据获取（bsk 通道）—— 用 bsk evaluate 在 ERP 页面上下文里发请求

为什么用 bsk 而不是 cdp.py：
  cdp.py 需要 Chrome 带 --remote-debugging-port=9222 启动（现在 Chrome 被已有实例接管，端口不通）
  bsk 走扩展通道，不需要调试端口，而且是老板真实登录的浏览器，ERP 登录态天然可用

前置：
  1. bsk daemon 常驻
  2. Chrome 装了 BrowserSkill 扩展并已连接
  3. **老板浏览器已登录 ERP**（http://erp.hzcwin.com）

用法：
  python erp_bsk.py --ping                      # 探活（拿总数）
  python erp_bsk.py --sku HJZ202610171732NJ98   # SKU → SPU（正向反查）
  python erp_bsk.py --spu HJZ20261017173        # SPU → 详情（含全部 skuList）
  python erp_bsk.py --sku xxx --full            # 反查 + 直接输出完整 SPU JSON
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

BSK = os.path.expanduser("~/.local/bin/bsk.exe")
if not os.path.exists(BSK):
    BSK = "bsk"
ENV = {**os.environ, "BSK_AUTO_START": "0"}

ERP_LIST = "http://api.hzcwin.com/hzkj-product/product/productLibrary/spu/list"
ERP_DETAIL = "http://api.hzcwin.com/hzkj-product/product/productLibrary/spu/queryById?id="



def _fmt_attrs(attrs):
    """attributes 可能是 list[dict] 或 JSON 字符串"""
    if not attrs:
        return "-"
    if isinstance(attrs, str):
        try:
            attrs = json.loads(attrs)
        except (json.JSONDecodeError, TypeError):
            return attrs[:40]
    if not isinstance(attrs, list):
        return str(attrs)[:40]
    parts = []
    for a in attrs:
        if isinstance(a, dict):
            parts.append(f"{a.get('name')}={a.get('value')}")
        else:
            parts.append(str(a))
    return ", ".join(parts) or "-"

def run(args, timeout=180):
    try:
        r = subprocess.run([BSK] + args, capture_output=True, text=True,
                           timeout=timeout, env=ENV, encoding="utf-8", errors="ignore")
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return -1, "timeout"
    except FileNotFoundError:
        return -2, "bsk not found"


def session_start():
    code, out = run(["session", "start", "--no-focus", "--json"], timeout=60)
    m = re.search(r'"session_id"\s*:\s*"([^"]+)"', out)
    if not m:
        raise RuntimeError(f"建 session 失败: {out[:200]}")
    return m.group(1)


def session_stop(sid):
    run(["session", "stop", sid], timeout=30)


def evaluate(sid, js, timeout=120):
    """在页面上下文执行 JS，返回字符串结果"""
    code, out = run(["evaluate", js, "--session", sid], timeout=timeout)
    if code != 0:
        raise RuntimeError(f"evaluate 失败: {out[:200]}")
    return out.strip()


def _js_common():
    """公共 JS：拿 token + 同步 POST"""
    return (
        "var tk=JSON.parse(localStorage.getItem('pro__Access-Token')).value;"
        "function _post(url,body){var x=new XMLHttpRequest();"
        "x.open('POST',url,false);"
        "x.setRequestHeader('Content-Type','application/json');"
        "x.setRequestHeader('X-Access-Token',tk);"
        "x.send(JSON.stringify(body));return JSON.parse(x.responseText);}"
        "function _get(url){var x=new XMLHttpRequest();"
        "x.open('GET',url,false);"
        "x.setRequestHeader('X-Access-Token',tk);"
        "x.send();return JSON.parse(x.responseText);}"
    )


def ensure_erp_page(sid):
    """确保当前页在 ERP 域下（token 只在 ERP 域可读）"""
    code, out = run(["evaluate", "location.hostname", "--session", sid], timeout=60)
    host = out.strip().strip('"')
    if "hzcwin" not in host:
        run(["navigate", "http://erp.hzcwin.com", "--session", sid], timeout=120)
        time.sleep(3)
        code, out = run(["evaluate", "location.hostname", "--session", sid], timeout=60)
        host = out.strip().strip('"')
        if "hzcwin" not in host:
            raise RuntimeError(f"未能进入 ERP 域（当前 {host}），老板可能未登录")
    return host


def ping(sid):
    js = f"(function(){{{_js_common()}var r=_post('{ERP_LIST}',{{pageNo:1,pageSize:1,productStatus:5}});return JSON.stringify({{code:r.code,total:r.result?r.result.total:null}});}})()"
    return json.loads(evaluate(sid, js))


def find_by_sku(sid, sku):
    """SKU → SPU（用 skuCode 参数精确过滤，实测有效）"""
    js = (f"(function(){{{_js_common()}"
          f"var r=_post('{ERP_LIST}',{{pageNo:1,pageSize:5,productStatus:5,skuCode:'{sku}'}});"
          f"var recs=(r.result&&r.result.records)||[];"
          f"return JSON.stringify({{n:recs.length,"
          f"list:recs.map(function(v){{return {{spu:v.spuCode,id:v.id,"
          f"name:(v.customs||{{}}).nameEn,status:v.productStatus}}}})}});}})()")
    return json.loads(evaluate(sid, js))


def find_by_code(sid, code, field="spuCode"):
    """SPU 编码 → SPU（field 用 spuCode）"""
    js = (f"(function(){{{_js_common()}"
          f"var r=_post('{ERP_LIST}',{{pageNo:1,pageSize:5,productStatus:5,{field}:'{code}'}});"
          f"var recs=(r.result&&r.result.records)||[];"
          f"return JSON.stringify({{n:recs.length,"
          f"list:recs.map(function(v){{return {{spu:v.spuCode,id:v.id,"
          f"name:(v.customs||{{}}).nameEn}}}})}});}})()")
    return json.loads(evaluate(sid, js))


def get_spu_detail(sid, spu_id):
    """拿 SPU 详情（含完整 skuList）"""
    js = (f"(function(){{{_js_common()}"
          f"var r=_get('{ERP_DETAIL}{spu_id}');"
          f"var d=r.result||{{}};"
          f"return JSON.stringify(d);}})()")
    raw = evaluate(sid, js, timeout=180)
    return json.loads(raw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ping", action="store_true")
    ap.add_argument("--sku", help="子体 SKU，反查它属于哪个 SPU")
    ap.add_argument("--spu", help="SPU 编码，拿详情")
    ap.add_argument("--spu-id", help="SPU 内部 id，拿详情")
    ap.add_argument("--full", action="store_true", help="反查后直接拿完整详情")
    ap.add_argument("--out", help="完整 JSON 输出路径")
    args = ap.parse_args()

    sid = session_start()
    print(f"session: {sid}")
    try:
        host = ensure_erp_page(sid)
        print(f"页面域: {host}")

        if args.ping:
            r = ping(sid)
            print(f"探活: code={r['code']} total={r['total']}")
            return 0

        if args.sku:
            r = find_by_sku(sid, args.sku)
            print(f"SKU {args.sku} → 匹配 {r['n']} 个 SPU")
            for x in r["list"]:
                print(f"  SPU {x['spu']}  id={x['id']}  {x.get('name','')}")
            if r["n"] == 0:
                print("⚠️ 反查不到，检查 SKU 是否在 productStatus=5（已完成）里")
                return 1
            if args.full:
                d = get_spu_detail(sid, r["list"][0]["id"])
                skus = d.get("skuList") or []
                print(f"\nSPU {d.get('spuCode')}: {len(skus)} 个变体")
                for s in skus:
                    attr_s = _fmt_attrs(s.get("attributes"))
                    imgs = s.get("imageList") or []
                    print(f"  {s.get('skuCode')}  {attr_s}  ¥{s.get('purchasePrice')}  "
                          f"{len(imgs)}张图")
                if args.out:
                    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
                    json.dump(d, open(args.out, "w", encoding="utf-8"),
                              ensure_ascii=False, indent=1)
                    print(f"\n完整 JSON → {args.out}")
            return 0

        if args.spu or args.spu_id:
            spu_id = args.spu_id
            if not spu_id:
                r = find_by_code(sid, args.spu)
                if r["n"] == 0:
                    print(f"⚠️ 找不到 SPU {args.spu}")
                    return 1
                spu_id = r["list"][0]["id"]
                print(f"SPU {args.spu} → id={spu_id}")
            d = get_spu_detail(sid, spu_id)
            skus = d.get("skuList") or []
            print(f"\nSPU {d.get('spuCode')}: {len(skus)} 个变体")
            for s in skus:
                attr_s = _fmt_attrs(s.get("attributes"))
                imgs = s.get("imageList") or []
                print(f"  {s.get('skuCode')}  {attr_s}  ¥{s.get('purchasePrice')}  {len(imgs)}张图")
            if args.out:
                os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
                json.dump(d, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                print(f"\n完整 JSON → {args.out}")
            return 0

        print("用法见 --help")
        return 0
    finally:
        session_stop(sid)


if __name__ == "__main__":
    sys.exit(main())
