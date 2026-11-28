import numpy as np
import pytest

from fraud.features import MODEL_FEATURES
from fraud.training.dataset import NO_ACCOUNT, load_paysim
from fraud.training.trainer import build_training_frame

CSV = """step,type,amount,nameOrig,oldbalanceOrg,newbalanceOrig,nameDest,oldbalanceDest,newbalanceDest,isFraud,isFlaggedFraud
1,PAYMENT,9839.64,C1231006815,170136.0,160296.36,M1979787155,0.0,0.0,0,0
1,TRANSFER,181.0,C1305486145,181.0,0.0,C553264065,0.0,0.0,1,0
2,CASH_OUT,181.0,C553264065,181.0,0.0,C38997010,21182.0,0.0,1,0
3,CASH_IN,500.0,C1305486145,0.0,500.0,C38997010,0.0,0.0,0,0
4,TRANSFER,10.0,C1,100.0,90.0,C1,0.0,0.0,0,0
"""


@pytest.fixture
def paysim(tmp_path):
    path = tmp_path / "paysim.csv"
    path.write_text(CSV)
    return load_paysim(path)


def test_maps_types_ids_and_drops_self_transfers(paysim):
    assert len(paysim) == 4
    assert list(paysim["type"].astype(str)) == ["W", "T", "W", "D"]
    assert list(paysim["ts"]) == [3600, 3600, 7200, 10800]
    assert paysim.loc[1, "recipient"] == paysim.loc[2, "account"]
    assert paysim.loc[0, "account"] != paysim.loc[0, "recipient"]
    assert paysim.loc[0, "recipient"] == NO_ACCOUNT
    assert np.isnan(paysim.loc[0, "recipient_balance"])


def test_training_frame_keeps_only_transfer_and_cash_out(paysim):
    frame = build_training_frame(paysim)
    assert len(frame) == 2
    assert frame["is_fraud"].tolist() == [1, 1]
    assert list(frame.columns[: len(MODEL_FEATURES)]) == list(MODEL_FEATURES)
