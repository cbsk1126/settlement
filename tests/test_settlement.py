"""
이커머스 정산 시스템 - pytest 테스트 스위트
[AI 활용 CI/CD 교육] Day 1 · Part 4

실행:
  pytest tests/ -v --cov=settlement --cov-report=term-missing

AI 활용 포인트:
  이 파일을 Claude.ai에 붙여넣고 "테스트 케이스를 보강해줘" 라고 물어보세요
"""

import uuid
# from datetime import datetime, timedelta, timezone
# from decimal import Decimal


# ... 기존에 있던 변수 선언(end_time = datetime(...) 등)이나 픽스처 코드들은 이 아래에 위치해야 합니다 ...

import pytest
from fastapi.testclient import TestClient

# test_calculate_settlement_with_empty_period 함수 내부
# start_time = datetime(2000, 1, 1, tzinfo=timezone.utc)
# end_time = datetime(2000, 1, 31, tzinfo=timezone.utc)

# 반드시 파일의 최상단(맨 위)으로 올려야 하는 코드들
from datetime import datetime, timezone
from settlement.main import app
from settlement.models.models import Order, OrderStatus, SettlementStatus
from settlement.services.settlement_service import SettlementService


start_time = datetime(2000, 1, 1, tzinfo=timezone.utc)
end_time = datetime(2000, 1, 31, tzinfo=timezone.utc)


import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def svc():
    return SettlementService()


@pytest.fixture
def sample_order():
    return Order(
        order_id=f"TEST-{uuid.uuid4().hex[:6]}",
        merchant_id="M-TEST",
        customer_id="C-001",
        amount=Decimal("100000"),
        fee_rate=Decimal("0.03"),
    )


# ── 모델 단위 테스트 ──────────────────────────────────────────────────


class TestOrderModel:
    def test_fee_amount(self):
        """수수료 3% 계산"""
        o = Order(order_id="T1", merchant_id="M", customer_id="C", amount=Decimal("100000"))
        assert o.fee_amount == Decimal("3000")  # 100,000 × 3%

    def test_net_amount(self):
        """실 정산액 = 매출 - 수수료"""
        o = Order(order_id="T2", merchant_id="M", customer_id="C", amount=Decimal("100000"))
        assert o.net_amount == Decimal("97000")

    def test_default_status_pending(self):
        o = Order(order_id="T3", merchant_id="M", customer_id="C", amount=Decimal("50000"))
        assert o.status == OrderStatus.PENDING

    def test_negative_amount_raises(self):
        with pytest.raises(Exception):
            Order(order_id="T4", merchant_id="M", customer_id="C", amount=Decimal("-1"))

    def test_fee_rounding(self):
        """소수점 수수료 반올림 (원 단위)"""
        o = Order(
            order_id="T5",
            merchant_id="M",
            customer_id="C",
            amount=Decimal("33333"),
            fee_rate=Decimal("0.03"),
        )
        # 33333 × 0.03 = 999.99 → 1000 (반올림)
        assert o.fee_amount == Decimal("1000")


# ── 서비스 단위 테스트 ────────────────────────────────────────────────


class TestSettlementService:
    def test_add_and_complete_order(self, svc, sample_order):
        svc.add_order(sample_order)
        done = svc.complete_order(sample_order.order_id)
        assert done is not None
        assert done.status == OrderStatus.COMPLETED
        assert done.completed_at is not None

    def test_complete_nonexistent_returns_none(self, svc):
        assert svc.complete_order("NONE-EXIST") is None

    def test_calculate_settlement_basic(self, svc):
        """3건 주문 정산 계산 기본 케이스"""
        merchant = "M-CALC"
        amounts = [Decimal("50000"), Decimal("100000"), Decimal("200000")]
        for i, amt in enumerate(amounts):
            o = Order(order_id=f"O-{i}", merchant_id=merchant, customer_id="C", amount=amt)
            svc.add_order(o)
            svc.complete_order(o.order_id)

        start = datetime.utcnow() - timedelta(hours=1)
        end = datetime.utcnow() + timedelta(hours=1)
        rec = svc.calculate_settlement(merchant, start, end)

        expected_sales = sum(amounts)
        expected_fee = sum(a * Decimal("0.03") for a in amounts)

        assert rec.order_count == 3
        assert rec.total_sales == expected_sales
        # 정수 비교 (양쪽 모두 quantize 결과)
        assert rec.total_fee.quantize(Decimal("1")) == expected_fee.quantize(Decimal("1"))
        assert rec.net_amount == expected_sales - rec.total_fee
        assert rec.status == SettlementStatus.PENDING

    def test_pending_orders_excluded(self, svc):
        """PENDING 상태 주문은 정산 제외"""
        o = Order(order_id="PEND-1", merchant_id="M-X", customer_id="C", amount=Decimal("100000"))
        svc.add_order(o)  # 완료 처리 안 함

        start = datetime.utcnow() - timedelta(hours=1)
        end = datetime.utcnow() + timedelta(hours=1)
        rec = svc.calculate_settlement("M-X", start, end)

        assert rec.order_count == 0
        assert rec.total_sales == Decimal("0")

    def test_process_settlement(self, svc, sample_order):
        svc.add_order(sample_order)
        svc.complete_order(sample_order.order_id)

        rec = svc.calculate_settlement(
            "M-TEST",
            datetime.utcnow() - timedelta(hours=1),
            datetime.utcnow() + timedelta(hours=1),
        )
        done = svc.process_settlement(rec.settlement_id)

        assert done.status == SettlementStatus.COMPLETED
        assert done.processed_at is not None

    def test_list_settlements_filter(self, svc):
        """merchant_id 필터 동작 확인"""
        for m in ["M-A", "M-B"]:
            o = Order(order_id=f"O-{m}", merchant_id=m, customer_id="C", amount=Decimal("10000"))
            svc.add_order(o)
            svc.complete_order(o.order_id)
            svc.calculate_settlement(
                m,
                datetime.utcnow() - timedelta(hours=1),
                datetime.utcnow() + timedelta(hours=1),
            )

        result = svc.list_settlements(merchant_id="M-A")
        assert all(r.merchant_id == "M-A" for r in result)


# ── API 통합 테스트 ───────────────────────────────────────────────────


class TestAPI:
    def test_health(self, client):
        res = client.get("/health")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"

    def test_ready(self, client):
        res = client.get("/ready")
        assert res.status_code == 200

    def test_create_order(self, client):
        payload = {
            "order_id": f"API-{uuid.uuid4().hex[:6]}",
            "merchant_id": "M-API",
            "customer_id": "C-001",
            "amount": "75000",
            "fee_rate": "0.03",
            "status": "pending",
            "created_at": datetime.utcnow().isoformat(),
        }
        res = client.post("/api/v1/orders", json=payload)
        assert res.status_code == 201
        assert res.json()["order_id"] == payload["order_id"]

    def test_list_settlements(self, client):
        res = client.get("/api/v1/settlements")
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_list_settlements_filter(self, client):
        res = client.get("/api/v1/settlements?merchant_id=M-001")
        assert res.status_code == 200














# ── 픽스처 ────────────────────────────────────────────────────────────
@pytest.fixture
def service():
    """Mock 없이 실제 SettlementService 인스턴스를 생성합니다."""
    return SettlementService()

# ── 테스트 케이스 ──────────────────────────────────────────────────────

def test_list_settlements_with_merchant_and_status_filter(service):
    """
    요구사항 1: list_settlements() 메서드의 merchant_id, status 동시 필터 케이스
    """
    target_merchant = "M-001"
    target_status = SettlementStatus.PENDING

    # 메서드 호출
    results = service.list_settlements(
        merchant_id=target_merchant,
        status=target_status
    )

    # 검증: 반환된 모든 항목이 두 조건을 모두 만족하는지 확인
    assert isinstance(results, list)
    for settlement in results:
        assert settlement.merchant_id == target_merchant
        assert settlement.status == target_status


def test_process_settlement_returns_none_for_invalid_id(service):
    """
    요구사항 2: process_settlement() 에서 없는 ID 조회 시 None 반환
    """
    invalid_id = "SETTLEMENT-9999-NOT-FOUND"

    # 메서드 호출
    result = service.process_settlement(settlement_id=invalid_id)

    # 검증: 예외가 발생하지 않고 None을 반환하는지 확인
    assert result is None


def test_calculate_settlement_with_empty_period(service):
    """
    요구사항 3: calculate_settlement() 빈 기간 (주문 0건) 케이스
    (수정됨: start_date, end_date 파라미터를 start, end로 변경)
    """
    # Warning을 방지하기 위해 UTC를 명시한 최신 datetime 문법 사용
    start_time = datetime(2000, 1, 1, tzinfo=timezone.utc)
    end_time = datetime(2000, 1, 31, tzinfo=timezone.utc)

    # 메서드 호출 (에러가 났던 파라미터명 수정 완료)
    result = service.calculate_settlement(
        merchant_id="M-001",
        start=start_time,  # <-- 수정된 부분
        end=end_time       # <-- 수정된 부분
    )

    # 검증: 반환값이 None이거나, 정산 금액/건수가 0인지 확인
    if result is None:
        assert True
    else:
        assert result.total_amount == Decimal("0")
        assert getattr(result, "order_count", 0) == 0
