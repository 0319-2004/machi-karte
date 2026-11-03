"""PLATEAU コードリスト（Building_usage.xml）の読み込み。

確認画面の用途選択肢は、埋め込み値ではなく codelist から読む。ファイルが無い
環境（data/ は git 管理外）では埋め込みラベルにフォールバックする。
"""
from __future__ import annotations

import os

from lxml import etree

from .db import DATA_DIR

GML = "{http://www.opengis.net/gml}"
USAGE_XML = os.path.join(DATA_DIR, "raw", "plateau", "2025",
                         "codelists", "Building_usage.xml")

# フォールバック（codelist が読めない場合）。461=不明は選択肢から外す。
USAGE_LABELS = {
    "401": "業務施設", "402": "商業施設", "403": "宿泊施設", "404": "商業系複合施設",
    "411": "住宅", "412": "共同住宅", "413": "店舗等併用住宅", "414": "店舗等併用共同住宅",
    "415": "作業所併用住宅", "421": "官公庁施設", "422": "文教厚生施設",
    "431": "運輸倉庫施設", "441": "工場", "451": "農林漁業用施設", "452": "供給処理施設",
    "453": "防衛施設", "454": "その他", "461": "不明",
}


def load_usage_codes(path: str = USAGE_XML) -> list[dict]:
    """[{code, label}] を codelist の順で返す（461=不明は除く）。"""
    codes: list[dict] = []
    try:
        tree = etree.parse(path)
        for defn in tree.iter(f"{GML}Definition"):
            name = defn.findtext(f"{GML}name")
            desc = defn.findtext(f"{GML}description")
            if name and name != "461":
                codes.append({"code": name.strip(), "label": (desc or name).strip()})
    except (OSError, etree.XMLSyntaxError):
        codes = [{"code": c, "label": lbl}
                 for c, lbl in USAGE_LABELS.items() if c != "461"]
    return codes


# code -> label の辞書（埋め込み＋codelist をマージ）
def usage_label_map() -> dict[str, str]:
    m = dict(USAGE_LABELS)
    for c in load_usage_codes():
        m[c["code"]] = c["label"]
    return m
