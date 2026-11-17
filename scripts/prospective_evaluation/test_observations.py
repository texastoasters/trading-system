"""Public-seam tests for bounded forward observations."""
from __future__ import annotations
import copy
from datetime import datetime
import pytest
from prospective_evaluation.observations import ObservationError, observe_market, reconstruct_observation
STARTED="2026-01-06T22:00:00+00:00"; COMPLETED="2026-01-06T22:01:00+00:00"
class Adapter:
 def __init__(self,r): self.responses=list(r); self.calls=[]
 def fetch(self,e,q):
  self.calls.append((e,copy.deepcopy(q))); x=self.responses.pop(0)
  if isinstance(x,BaseException): raise x
  return copy.deepcopy(x)
class Clock:
 def __init__(self,*v): self.v=iter(datetime.fromisoformat(x) for x in v)
 def __call__(self): return next(self.v)
def receipt(p,request=STARTED,received="2026-01-06T22:00:10+00:00",digest="a"*64,rate=None): return {"payload":p,"request_started_at":request,"received_at":received,"body_sha256":digest,"rate_limit":rate if rate is not None else {"limit":200,"remaining":199,"reset":1}}
def session(day="2026-01-05",close="2000"): return {"date":day,"open":"09:30","close":"16:00","session_open":"0400","session_close":close,"settlement_date":day}
def bar(day="2026-01-05",**c):
 v={"t":f"{day}T05:00:00Z","o":"100","h":"103","l":"99","c":"102","v":1000}; v.update(c); return v
def capture(symbols=None): return observe_market(symbols or ["SPY"],"2026-01-01","2026-01-09",Adapter([receipt([session()]),receipt({"bars":{"SPY":[bar()]},"next_page_token":None},request="2026-01-06T22:00:11+00:00",received="2026-01-06T22:00:20+00:00")]),clock=Clock(STARTED,COMPLETED))

def test_public_capture_and_normalize():
 a=Adapter([receipt([session()]),receipt({"bars":{"SPY":[]},"next_page_token":"next"},request="2026-01-06T22:00:11+00:00",received="2026-01-06T22:00:20+00:00"),receipt({"bars":{"SPY":[bar()]},"next_page_token":None},request="2026-01-06T22:00:21+00:00",received="2026-01-06T22:00:30+00:00")])
 o=observe_market(["SPY"],"2026-01-01","2026-01-09",a,clock=Clock(STARTED,COMPLETED)); assert [x[0] for x in a.calls]==["sessions","bars","bars"]
 assert a.calls[0][1]=={"start":"2026-01-01","end":"2026-01-09","date_type":"TRADING"}; assert a.calls[1][1]["start"]=="2026-01-01T00:00:00-05:00"; assert a.calls[2][1]["page_token"]=="next"
 n=reconstruct_observation(o); assert n["sessions"][0]=={"date":"2026-01-05","interval_start":"2026-01-05T05:00:00+00:00","session_close_at":"2026-01-06T01:00:00+00:00"}; assert n["bars"]["SPY"][0]["available_at"]=="2026-01-06T22:00:30+00:00"; assert n["bars"]["SPY"][0]["source_published_at"] is None

def test_boundaries_future_calendar_missing_symbol():
 for request,count in [("2026-01-06T00:59:59+00:00",0),("2026-01-06T01:00:00+00:00",0),("2026-01-06T01:00:01+00:00",1)]:
  o=capture(); o["started_at"]="2026-01-05T23:00:00+00:00"; sp=o["sources"]["sessions"]["pages"][0]; sp["request_started_at"]="2026-01-05T23:00:00+00:00"; sp["received_at"]="2026-01-05T23:00:01+00:00"; p=o["sources"]["bars"]["pages"][0]; p["query"]["end"]=o["started_at"]; p["request_started_at"]=request; p["received_at"]="2026-01-06T01:00:02+00:00"; assert len(reconstruct_observation(o)["bars"]["SPY"])==count
 o=capture(["SPY","QQQ"]); sp=o["sources"]["sessions"]["pages"][0]; sp["payload"].append(session("2026-01-08")); sp["received_at"]="2026-01-06T22:00:55+00:00"; bp=o["sources"]["bars"]["pages"][0]; bp["request_started_at"]="2026-01-06T22:00:56+00:00"; bp["received_at"]="2026-01-06T22:00:57+00:00"; n=reconstruct_observation(o); assert n["bars"]["QQQ"]==[] and n["bars"]["SPY"][0]["available_at"]=="2026-01-06T22:00:57+00:00"

def test_partial_failure_sanitized():
 o=observe_market(["SPY"],"2026-01-01","2026-01-09",Adapter([receipt([session()]),receipt({"bars":{"SPY":[]},"next_page_token":"x"},request="2026-01-06T22:00:11+00:00",received="2026-01-06T22:00:12+00:00"),RuntimeError("SECRET")]),clock=Clock(STARTED,COMPLETED)); assert len(o["sources"]["bars"]["pages"])==1 and "SECRET" not in repr(o)
 with pytest.raises(ObservationError,match="capture_failed"): reconstruct_observation(o)
class Coded(Exception):
 def __init__(self,c,s=None): self.code=c; self.http_status=s
@pytest.mark.parametrize("f,c,s",[(Coded("missing_credentials",401),"credentials_unavailable",401),(Coded("invalid_credentials",True),"credentials_unavailable",None),(Coded("invalid_params"),"invalid_request",None),(Coded("invalid_endpoint"),"invalid_request",None),(Coded("response_too_large"),"invalid_source_response",None),(Coded("invalid_clock"),"invalid_source_response",None),(Coded([]),"source_unavailable",None),(ValueError(),"invalid_source_response",None)])
def test_error_mapping(f,c,s):
 o=observe_market(["SPY"],"2026-01-01","2026-01-09",Adapter([f,receipt({"bars":{},"next_page_token":None})]),clock=Clock(STARTED,COMPLETED)); assert o["sources"]["sessions"]["error"]=={"code":c,"http_status":s}
@pytest.mark.parametrize("args",[([],"2026-01-01","2026-01-09",16,None),(["bad symbol"],"2026-01-01","2026-01-09",16,None),(["SPY","SPY"],"2026-01-01","2026-01-09",16,None),(["SPY"],"bad","2026-01-09",16,None),(["SPY"],"2026-02-30","2026-03-01",16,None),(["SPY"],"2026-01-09","2026-01-01",16,None),(["SPY"],"2026-01-01","2027-01-07",16,None),(["SPY"],"2026-01-01","2026-01-09",True,None),(["SPY"],"2026-01-01","2026-01-09",17,None),(["SPY"],"2026-01-01","2026-01-09",16,"bad")])
def test_invalid_request(args):
 a=Adapter([]); sy,st,en,mp,cl=args; kw={"max_pages":mp};
 if cl is not None: kw["clock"]=cl
 with pytest.raises(ObservationError,match="invalid_request"): observe_market(sy,st,en,a,**kw)
 assert not a.calls
def test_invalid_clock():
 for x in (datetime(2026,1,1),"bad"):
  with pytest.raises(ObservationError,match="invalid_clock"): observe_market(["SPY"],"2026-01-01","2026-01-09",Adapter([]),clock=lambda x=x:x)
@pytest.mark.parametrize("p",[None,[{"date":"x"}],[{**session(),"open":1}]])
def test_bad_sessions(p):
 o=observe_market(["SPY"],"2026-01-01","2026-01-09",Adapter([receipt(p),receipt({"bars":{},"next_page_token":None})]),clock=Clock(STARTED,COMPLETED)); assert o["sources"]["sessions"]["error"]["code"]=="invalid_source_response"
@pytest.mark.parametrize("p",[{}, {"bars":{"BAD":[]},"next_page_token":None},{"bars":{"SPY":[]},"next_page_token":""},{"bars":{"SPY":"x"},"next_page_token":None},{"bars":{"SPY":[{"t":"x"}]},"next_page_token":None},{"bars":{"SPY":[bar(t=1)]},"next_page_token":None},{"bars":{"SPY":[bar(v=True)]},"next_page_token":None}])
def test_bad_bars(p):
 o=observe_market(["SPY"],"2026-01-01","2026-01-09",Adapter([receipt([session()]),receipt(p)]),clock=Clock(STARTED,COMPLETED)); assert o["sources"]["bars"]["error"]["code"]=="invalid_source_response"
def test_bad_receipts():
 for x in ({"payload":[]},receipt([],request="bad"),receipt([],request="2026-01-06T22:00:10+00:00",received=STARTED),receipt([],digest="bad"),receipt([],rate={"limit":True})):
  assert observe_market(["SPY"],"2026-01-01","2026-01-09",Adapter([x,receipt({"bars":{},"next_page_token":None})]),clock=Clock(STARTED,COMPLETED))["sources"]["sessions"]["complete"] is False

def test_limits():
 eq=[bar() for _ in range(20000)]; assert observe_market(["SPY"],"2026-01-01","2026-01-09",Adapter([receipt([session()]),receipt({"bars":{"SPY":eq},"next_page_token":None})]),clock=Clock(STARTED,COMPLETED))["sources"]["bars"]["complete"]
 above=eq+[bar()]; assert observe_market(["SPY"],"2026-01-01","2026-01-09",Adapter([receipt([session()]),receipt({"bars":{"SPY":above},"next_page_token":None})]),clock=Clock(STARTED,COMPLETED))["sources"]["bars"]["error"]["code"]=="invalid_source_response"
 rec=[bar() for _ in range(10001)]; assert observe_market(["SPY"],"2026-01-01","2026-01-09",Adapter([receipt([session()]),receipt({"bars":{"SPY":rec},"next_page_token":"x"}),receipt({"bars":{"SPY":rec},"next_page_token":None})]),clock=Clock(STARTED,COMPLETED))["sources"]["bars"]["error"]["code"]=="invalid_source_response"
 pages=[receipt({"bars":{},"next_page_token":f"p{i}"}) for i in range(16)]; assert observe_market(["SPY"],"2026-01-01","2026-01-09",Adapter([receipt([session()]),*pages]),clock=Clock(STARTED,COMPLETED))["sources"]["bars"]["error"]["code"]=="pagination_limit"
 cycle=Adapter([receipt([session()]),receipt({"bars":{},"next_page_token":"x"}),receipt({"bars":{},"next_page_token":"x"})]); assert observe_market(["SPY"],"2026-01-01","2026-01-09",cycle,clock=Clock(STARTED,COMPLETED))["sources"]["bars"]["error"]["code"]=="pagination_cycle"

@pytest.mark.parametrize("m",["root","schema","symbols","date","started_type","started_bad","started_naive","completed","policy","sources","source_shape","failed","pages","page_shape","chronology","ordinal","query","hash","rates","terminal","cycle","session_date","duplicate_session","close_format","close_value","unknown","nonmidnight","future","zero","nan","bool","decimal","volume","range","duplicate_bar","missing"])
def test_offline_corruption(m):
 o=capture(); sp=o["sources"]["sessions"]["pages"][0]; bp=o["sources"]["bars"]["pages"][0]; i=bp["payload"]["bars"]["SPY"][0]
 if m=="root": o["x"]=1
 elif m=="schema": o["schema_version"]=True
 elif m=="symbols": o["symbols"]=[]
 elif m=="date": o["start_date"]="2026-99-99"
 elif m=="started_type": o["started_at"]=1
 elif m=="started_bad": o["started_at"]="bad"
 elif m=="started_naive": o["started_at"]="2026-01-06T22:00:00"
 elif m=="completed": o["completed_at"]="2026-01-01T00:00:00+00:00"
 elif m=="policy": o["policy"]["feed"]="sip"
 elif m=="sources": o["sources"]=[]
 elif m=="source_shape": o["sources"]["bars"]["x"]=1
 elif m=="failed": o["sources"]["bars"]["complete"]=False
 elif m=="pages": o["sources"]["bars"]["pages"]=[]
 elif m=="page_shape": bp["x"]=1
 elif m=="chronology": bp["received_at"]="2026-01-01T00:00:00+00:00"
 elif m=="ordinal": bp["ordinal"]=2
 elif m=="query": bp["query"]["limit"]=1
 elif m=="hash": bp["body_sha256"]="bad"
 elif m=="rates": bp["rate_limit"]={"x":1}
 elif m=="terminal": bp["payload"]["next_page_token"]="x"
 elif m=="cycle":
  bp["payload"]["next_page_token"]="x"; x=copy.deepcopy(bp); x["ordinal"]=2; x["query"]["page_token"]="x"; x["request_started_at"]="2026-01-06T22:00:21+00:00"; x["received_at"]="2026-01-06T22:00:22+00:00"; o["sources"]["bars"]["pages"].append(x)
 elif m=="session_date": sp["payload"][0]["date"]="2026-99-99"
 elif m=="duplicate_session": sp["payload"].append(session())
 elif m=="close_format": sp["payload"][0]["session_close"]="20:00"
 elif m=="close_value": sp["payload"][0]["session_close"]="2460"
 elif m=="unknown": i["t"]="2026-01-04T05:00:00Z"
 elif m=="nonmidnight": i["t"]="2026-01-05T05:00:01Z"
 elif m=="future": i["t"]="2026-01-08T05:00:00Z"
 elif m=="zero": i["o"]="0"
 elif m=="nan": i["h"]="NaN"
 elif m=="bool": i["o"]=True
 elif m=="decimal": i["o"]="bad"
 elif m=="volume": i["v"]=-1
 elif m=="range": i["o"]="200"
 elif m=="duplicate_bar": bp["payload"]["bars"]["SPY"].append({**i,"c":"101"})
 else: del i["o"]
 with pytest.raises(ObservationError): reconstruct_observation(o)

def test_exotic_failure_and_controls():
 class Exotic(BaseException): pass
 o=observe_market(["SPY"],"2026-01-01","2026-01-09",Adapter([receipt([session()]),Exotic()]),clock=Clock(STARTED,COMPLETED)); assert o["sources"]["bars"]["error"]["code"]=="source_unavailable"
 for control in (KeyboardInterrupt(),SystemExit(),GeneratorExit()):
  with pytest.raises(type(control)): observe_market(["SPY"],"2026-01-01","2026-01-09",Adapter([control]),clock=Clock(STARTED,COMPLETED))
  with pytest.raises(type(control)): observe_market(["SPY"],"2026-01-01","2026-01-09",Adapter([receipt([session()]),control]),clock=Clock(STARTED,COMPLETED))

def test_identical_duplicate_uses_latest_actual_receipt():
 o=capture(); first=o["sources"]["bars"]["pages"][0]; first["payload"]["next_page_token"]="x"; second=copy.deepcopy(first); second["ordinal"]=2; second["query"]["page_token"]="x"; second["payload"]["next_page_token"]=None; second["request_started_at"]="2026-01-06T22:00:21+00:00"; second["received_at"]="2026-01-06T22:00:22+00:00"; o["sources"]["bars"]["pages"].append(second)
 assert reconstruct_observation(o)["bars"]["SPY"][0]["available_at"]=="2026-01-06T22:00:22+00:00"
