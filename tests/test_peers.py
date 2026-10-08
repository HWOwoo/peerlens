from peerlens.agent.peers import GROUP_OF, UNIVERSE, PeerFinder, Profile, group_score


def _p(t, sic, emb, rev=100.0):
    return Profile(t, t, sic, "", rev, "USD", "", emb, "m")


class _Finder(PeerFinder):
    def __init__(self, profiles):
        self.profiles = {p.ticker: p for p in profiles}

    def profile(self, ticker):
        return self.profiles.get(ticker)


def test_universe_has_50_unique_with_groups():
    assert len(UNIVERSE) == len(set(UNIVERSE)) == 50
    assert GROUP_OF["SNDK"] == GROUP_OF["MU"] and GROUP_OF["ASML"] == GROUP_OF["AMAT"]
    assert group_score("메모리·저장장치", "반도체 설계·IDM") == (0.5, "인접")
    assert group_score(None, "EDA") == (0.0, "후보군 밖")


def test_same_subindustry_beats_shared_two_digit_sic():
    """ASML(SIC 3559)의 Peer: 같은 장비 업종 AMAT(SIC 3674)가 SIC 앞 두 자리만 같은 DELL(3571)보다 위."""
    finder = _Finder([
        _p("ASML", "3559", [1.0, 0.0]),
        _p("AMAT", "3674", [0.8, 0.6]),
        _p("DELL", "3571", [0.75, 0.66]),
        _p("CRM", "7372", [0.0, 1.0]),
    ])
    cands = finder.find("ASML", k=3, universe=["AMAT", "DELL", "CRM"])
    assert [c.ticker for c in cands] == ["AMAT", "DELL", "CRM"]
    amat = cands[0]
    assert amat.group == "반도체 장비·소재" and amat.group_match == "동일"
    assert "세부 업종" in amat.reason and "후보 중 1.00" in amat.reason  # 유사도는 후보 안에서 0~1
