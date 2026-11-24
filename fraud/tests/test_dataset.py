import tempfile
from pathlib import Path

import numpy as np
from django.test import SimpleTestCase

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


class LoadPaysimTests(SimpleTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "paysim.csv"
        self.path.write_text(CSV)

    def test_maps_types_ids_and_drops_self_transfers(self):
        events = load_paysim(self.path)

        self.assertEqual(len(events), 4)  # self-transfer removed
        self.assertEqual(list(events["type"].astype(str)), ["W", "T", "W", "D"])
        self.assertEqual(list(events["ts"]), [3600, 3600, 7200, 10800])
        # The transfer's recipient is the same account that later cashes out.
        self.assertEqual(events.loc[1, "recipient"], events.loc[2, "account"])
        # Customer and merchant with the same digits must not collide.
        self.assertNotEqual(events.loc[0, "account"], events.loc[0, "recipient"])
        self.assertEqual(events.loc[0, "recipient"], NO_ACCOUNT)
        self.assertTrue(np.isnan(events.loc[0, "recipient_balance"]))

    def test_training_frame_keeps_only_transfer_and_cash_out(self):
        frame = build_training_frame(load_paysim(self.path))
        self.assertEqual(len(frame), 2)
        self.assertEqual(frame["is_fraud"].tolist(), [1, 1])
        self.assertEqual(list(frame.columns[: len(MODEL_FEATURES)]), list(MODEL_FEATURES))
