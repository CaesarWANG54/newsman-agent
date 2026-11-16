"""Synthetic review records, not real company facts or human approvals."""

from copy import deepcopy
from datetime import datetime
import hashlib
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

from newsman.config import Settings
from newsman.services.selection import news_window

NOW = datetime.fromisoformat("2026-10-25T10:00:00+08:00")


def fixtures():
    window = news_window(Settings(_env_file=None), "2026-10-25", now=NOW)
    specs = [
        [
            (
                "demand",
                "Clinic Atlas requests help checking medical coding queues.",
                "测试诊所要求协助检查医疗编码队列。",
            ),
            (
                "workflow",
                "Staff compare records with coding rules manually.",
                "员工手动比较记录与编码规则。",
            ),
            (
                "event",
                "Clinic Atlas starts a supervised coding pilot.",
                "测试诊所启动有人监督的编码试点。",
            ),
            ("sector", "The pilot covers clinical workflow software.", "试点涵盖临床工作流软件。"),
            (
                "company_exposure",
                "ExampleCo (EXM) supplies coding software to Clinic Atlas.",
                "测试公司 ExampleCo 为测试诊所提供编码软件。",
            ),
        ],
        [
            (
                "alternative",
                "Manual review remains available without a new software contract.",
                "无需新的软件合同仍可进行人工审核。",
            ),
            ("counterevidence", "Pilot procurement has not been approved.", "试点采购尚未获批。"),
            (
                "risk",
                "Patient records cannot leave the clinic under the pilot rules.",
                "试点规则不允许患者记录离开诊所。",
            ),
        ],
    ]
    articles = []
    for index, rows in enumerate(specs):
        source_id = str(uuid5(NAMESPACE_URL, f"fixture-source-{index}"))
        url = f"https://review-{index}.invalid/clinical-pilot"
        body = "\n".join(en for _, en, _ in rows)
        facts = []
        for role, en, zh in rows:
            fact = dict(
                id=role,
                role=role,
                text_en=en,
                text_zh=zh,
                quote=en,
                start=body.index(en),
                end=body.index(en) + len(en),
                valid_from="2026-10-25T06:00:00+08:00",
                valid_until="2026-10-26T08:00:00+08:00",
            )
            if role == "company_exposure":
                fact.update(entity="ExampleCo", symbol="EXM")
            facts.append(fact)
        review = dict(
            version="insight-evidence-v1",
            source_id=source_id,
            url=url,
            body_sha256=hashlib.sha256(body.encode()).hexdigest(),
            reviewed_at="2026-10-25T09:00:00+08:00",
            reviewer="synthetic-fixture-not-real-approval",
            method="human_source_review",
            event_id="fixture-clinical-pilot",
            topic="clinical-coding",
            independence_group=f"fixture-owner-{index}",
            conflict_status="clear",
            facts=facts,
        )
        articles.append(
            SimpleNamespace(
                id=uuid5(NAMESPACE_URL, url),
                source_id=source_id,
                canonical_url=url,
                url=url,
                title="Clinic Atlas coding pilot",
                text_content=body,
                published_at=datetime.fromisoformat("2026-10-25T06:00:00+08:00"),
                fetched_at=datetime.fromisoformat("2026-10-25T07:00:00+08:00"),
                category="business",
                tags=[],
                importance_score=0.7,
                summary=None,
                why_it_matters=None,
                cluster_key="fixture-pilot",
                source=SimpleNamespace(credibility=0.8),
                metadata_json={
                    "source_name": f"Fixture Review {index}",
                    "extraction_status": "available",
                    "body_complete": True,
                    "verification_status": "not_verified",
                    "insight_evidence": review,
                },
            )
        )
    return articles, window


class MockProvider:
    name = "offline_mock_v1"

    def __init__(self):
        self.calls = []

    def propose(self, kind, context):
        self.calls.append(kind)

        def fact(role):
            return dict(id=role, role=role, kind="fact", fact_ids=[role])

        def derived(role, typ, en, zh, refs, counters=None):
            return dict(
                id=role,
                role=role,
                kind=typ,
                text_en=en,
                text_zh=zh,
                fact_ids=refs,
                counter_fact_ids=counters or [],
            )

        if kind == "startup":
            claims = [
                fact("demand"),
                fact("workflow"),
                fact("alternative"),
                fact("risk"),
                derived(
                    "customer",
                    "hypothesis",
                    "Coding supervisors may be the first interview audience.",
                    "编码主管可能是首轮访谈对象。",
                    ["demand"],
                ),
                derived(
                    "solution",
                    "hypothesis",
                    "Test a local review checklist with human sign-off.",
                    "尝试带人工签核的本地审核清单。",
                    ["workflow", "risk"],
                ),
                derived(
                    "validation",
                    "validation",
                    "Interview two staff using a paper checklist before building software.",
                    "开发软件前，用纸质清单访谈两名员工。",
                    ["workflow", "alternative"],
                ),
            ]
            companies = []
        else:
            claims = [
                fact("event"),
                fact("sector"),
                fact("counterevidence"),
                derived(
                    "transmission",
                    "inference",
                    "The pilot may require implementation work only if procurement approves it.",
                    "只有采购获批，试点才可能产生实施需求。",
                    ["event", "company_exposure"],
                    ["counterevidence"],
                ),
                derived(
                    "scenario",
                    "hypothesis",
                    "Assume no recurring sales until a paid contract is disclosed.",
                    "有付费合同披露前，不假设存在持续销售。",
                    ["counterevidence"],
                ),
                derived(
                    "indicator",
                    "validation",
                    "Check for a signed contract and disclosed delivery costs.",
                    "后续核对签约合同与披露的交付成本。",
                    ["event", "counterevidence"],
                ),
            ]
            companies = [dict(entity="ExampleCo", symbol="EXM", fact_ids=["company_exposure"])]
        return deepcopy(
            dict(
                decision="analyze", reason="evidence_supported", claims=claims, companies=companies
            )
        )
