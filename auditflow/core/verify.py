from __future__ import annotations

from typing import List, Optional, Dict, Any
from dataclasses import dataclass

from auditflow.core.canonical import compute_payload_hash
from auditflow.util.hashing import sha256_text
from auditflow.store.runs import get_run
from auditflow.store.evidence import get_evidence_by_run
from auditflow.store.chain import get_chain_by_run


@dataclass
class VerifyResult:
    is_valid: bool
    run_id: str
    evidence_count: int
    message: str
    mismatch_seq: Optional[int] = None
    details: Dict[str, Any] = None


def verify_run(conn, run_id: str) -> VerifyResult:
    """
    重新演算整個雜湊鏈，驗證資料完整性。
    邏輯：
    1. 重新計算每一筆證據的 payload_hash。
    2. 從 run_seed_hash 開始，模擬當初的串接邏輯重新算一次 this_hash。
    3. 比對算出來的結果跟資料庫存的是否一致。
    """
    # 1) 讀取 Run 資訊 (取得創世雜湊與密封雜湊)
    run = get_run(conn, run_id)
    if not run:
        return VerifyResult(False, run_id, 0, "找不到該 Run ID")
    
    if run["status"] == "RUNNING":
        return VerifyResult(False, run_id, 0, "該 Run 尚未封印 (RUNNING)，無法驗證")

    # 2) 讀取該 Run 的所有鏈路與證據 (依 seq 排序)
    # 這裡預期 get_chain_by_run 與 get_evidence_by_run 是按 seq/ts 排序好的
    chain_rows = get_chain_by_run(conn, run_id)
    evidence_map = {e["evidence_id"]: e for e in get_evidence_by_run(conn, run_id)}

    if len(chain_rows) != run["evidence_count"]:
        return VerifyResult(False, run_id, len(chain_rows), 
                            f"數量不符：資料庫紀錄 {run['evidence_count']} 筆，實際鏈路 {len(chain_rows)} 筆")

    # 3) 核心校驗迴圈
    current_prev_hash = run["run_seed_hash"]  # 從 Genesis 開始
    
    for i, row in enumerate(chain_rows, start=1):
        seq = row["seq"]
        evi_id = row["evidence_id"]
        evidence = evidence_map.get(evi_id)
        
        if not evidence:
            return VerifyResult(False, run_id, len(chain_rows), f"鏈路斷裂：找不到證據 ID {evi_id}", mismatch_seq=seq)

        # A) 重新計算該筆證據的 payload_hash (檢查原始資料是否被改過)
        recomputed_payload_hash = compute_payload_hash(
            evidence["payload_json"],
            evidence["attachment_sha256"],
            evidence["attachment_size"]
        )
        
        if recomputed_payload_hash != evidence["payload_hash"]:
            return VerifyResult(False, run_id, len(chain_rows), 
                                f"證據內容篡改：Seq {seq} 的內容雜湊不符", mismatch_seq=seq)

        # B) 重新串接雜湊鏈 (檢查鏈路是否被重新建構)
        # Spec: this_hash = sha256(prev_hash || run_id || seq || evidence_id || payload_hash)
        material = "||".join([
            current_prev_hash,
            run_id,
            str(seq),
            evi_id,
            recomputed_payload_hash
        ])
        recomputed_this_hash = sha256_text(material)

        if recomputed_this_hash != row["this_hash"]:
            return VerifyResult(False, run_id, len(chain_rows), 
                                f"鏈路雜湊失效：Seq {seq} 的鏈路指標錯誤", mismatch_seq=seq)

        # 通過驗證，移動到下一個節點
        current_prev_hash = recomputed_this_hash

    # 4) 最後封印校驗 (Final Seal)
    if current_prev_hash != run["final_chain_hash"]:
        return VerifyResult(False, run_id, len(chain_rows), "封印雜湊不符：最終雜湊值與 Runs 表紀錄不一致")

    return VerifyResult(True, run_id, len(chain_rows), "驗證通過：資料完整且無篡改痕跡")