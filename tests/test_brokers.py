from datetime import date

from qist.brokers.models import (Instruction, MultiLegOrder, OptionContract,
                                 OptionType, OrderLeg)
from qist.brokers.schwab_client import (build_schwab_order_payload,
                                        parse_quotes)
from qist.brokers.schwab_stream import parse_book_content


def test_osi_symbol_formatting():
    c = OptionContract(underlying="SPY", expiry=date(2024, 9, 20),
                       strike=450.0, option_type=OptionType.CALL)
    assert c.osi_symbol == "SPY   240920C00450000"
    assert len(c.osi_symbol) == 21


def test_parse_quotes_equity_and_option():
    payload = {
        "SPY": {"quote": {"bidPrice": 449.9, "askPrice": 450.1,
                          "lastPrice": 450.0, "totalVolume": 1000}},
        "XSP   240920P00440000": {"quote": {
            "bidPrice": 1.0, "askPrice": 1.2, "delta": -0.16,
            "volatility": 18.5, "openInterest": 500}},
    }
    quotes = parse_quotes(payload)
    assert quotes["SPY"].mid == 450.0
    assert quotes["SPY"].spread > 0
    opt = quotes["XSP   240920P00440000"]
    assert opt.mid == 1.1
    assert opt.delta == -0.16


def test_build_order_payload_vertical():
    legs = [
        OrderLeg(OptionContract("XSP", date(2024, 9, 20), 440, OptionType.PUT),
                 Instruction.SELL_TO_OPEN, 1),
        OrderLeg(OptionContract("XSP", date(2024, 9, 20), 435, OptionType.PUT),
                 Instruction.BUY_TO_OPEN, 1),
    ]
    order = MultiLegOrder(legs=legs, net_price=0.80, order_type="NET_CREDIT")
    payload = build_schwab_order_payload(order)
    assert payload["orderType"] == "NET_CREDIT"
    assert payload["complexOrderStrategyType"] == "VERTICAL"
    assert payload["price"] == 0.8
    assert len(payload["orderLegCollection"]) == 2
    assert payload["orderLegCollection"][0]["instruction"] == "SELL_TO_OPEN"


def test_build_order_payload_iron_condor_label():
    def put(k, instr):
        return OrderLeg(OptionContract("XSP", date(2024, 9, 20), k, OptionType.PUT),
                        instr, 1)

    def call(k, instr):
        return OrderLeg(OptionContract("XSP", date(2024, 9, 20), k, OptionType.CALL),
                        instr, 1)
    legs = [put(440, Instruction.SELL_TO_OPEN), put(435, Instruction.BUY_TO_OPEN),
            call(460, Instruction.SELL_TO_OPEN), call(465, Instruction.BUY_TO_OPEN)]
    payload = build_schwab_order_payload(MultiLegOrder(legs=legs, net_price=1.2))
    assert payload["complexOrderStrategyType"] == "IRON_CONDOR"


def test_parse_book_content_orders_and_microprice():
    content = {
        "key": "SPY",
        "1": 1_700_000_000_000,
        "2": [{"0": 449.9, "1": 300, "2": 3}, {"0": 449.8, "1": 500, "2": 2}],
        "3": [{"0": 450.1, "1": 100, "2": 1}, {"0": 450.2, "1": 400, "2": 4}],
    }
    book = parse_book_content(content)
    assert book.best_bid.price == 449.9
    assert book.best_ask.price == 450.1
    # microprice between bid and ask, weighted toward larger opposing size
    assert 449.9 < book.microprice < 450.1
    # bid size (300) < ask size (100)? best-level imbalance over depth 5
    imb = book.imbalance(depth=5)
    assert -1.0 <= imb <= 1.0
