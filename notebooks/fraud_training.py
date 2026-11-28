import marimo

__generated_with = "0.26.0"
app = marimo.App(width="medium", app_title="Fraud model training")


with app.setup:
    from pathlib import Path

    import altair as alt
    import marimo as mo
    import numpy as np
    import pandas as pd
    from sklearn.inspection import permutation_importance
    from sklearn.metrics import confusion_matrix, precision_recall_curve

    from fraud.features import MODEL_FEATURES
    from fraud.training import trainer
    from fraud.training.dataset import cached_events

    ROOT = Path(__file__).resolve().parents[1]
    ARTIFACTS = ROOT / "ml" / "artifacts"
    DATASET = ROOT / "ml" / "data" / "paysim.csv"


@app.cell
def _():
    mo.md("""
    # Fraud model training

    Walks through the same steps as `bank_manage train_fraud_model`, using the project's own training
    code: the PaySim dataset, the engineered features, the candidate models, the review and block
    thresholds, and which features the chosen model relies on.
    """)
    return


@app.cell
def _():
    events = pd.read_parquet(cached_events(DATASET, ARTIFACTS / "paysim_events.parquet"))
    return (events,)


@app.cell
def _(events):
    by_type = (events.groupby("paysim_type", observed=True)["is_fraud"].agg(transactions="size", fraud="sum")
               .assign(fraud_rate=lambda d: d.fraud / d.transactions).reset_index())
    rate_chart = alt.Chart(by_type).mark_bar().encode(
        x=alt.X("paysim_type:N", title="PaySim type", sort="-y"),
        y=alt.Y("fraud_rate:Q", title="Fraud rate", axis=alt.Axis(format="%")),
        tooltip=["paysim_type", "transactions", "fraud", alt.Tooltip("fraud_rate:Q", format=".3%")],
    ).properties(height=220)
    mo.vstack([
        mo.md(f"## The data\n**{len(events):,}** transactions, **{int(events.is_fraud.sum()):,}** fraudulent. "
              "Fraud only appears in TRANSFER and CASH_OUT, so those are the rows the model learns from."),
        mo.hstack([mo.ui.table(by_type, selection=None), rate_chart], widths=[1, 1]),
    ])
    return


@app.cell
def _(events):
    runs = sorted(ARTIFACTS.glob("runs/*/training_frame.parquet"))
    frame = pd.read_parquet(runs[-1]) if runs else trainer.build_training_frame(events)
    frame_source = f"run `{runs[-1].parent.name}`" if runs else "freshly built features"
    return frame, frame_source


@app.cell
def _():
    sample = mo.ui.slider(0.02, 1.0, step=0.02, value=0.1, debounce=True, show_value=True,
                          label="Share of the training frame to use")
    candidates = mo.ui.multiselect(options=list(trainer.CANDIDATES), value=["hgb", "logreg_balanced"],
                                   label="Candidate models")
    mo.vstack([mo.md("## Train candidates"), sample, candidates])
    return candidates, sample


@app.cell
def _(frame, frame_source, sample):
    sampled = frame.sample(frac=sample.value, random_state=trainer.RANDOM_STATE) if sample.value < 1 else frame
    train, valid, test = trainer.split(sampled)
    mo.md(f"Using {frame_source}: **{len(sampled):,}** rows, split out-of-time into "
          f"{len(train):,} train / {len(valid):,} validation / {len(test):,} test.")
    return test, train, valid


@app.cell
def _(candidates, train, valid):
    mo.stop(not candidates.value, mo.md("Pick at least one candidate."))
    x_train, y_train = trainer.xy(train)
    x_valid, y_valid = trainer.xy(valid)
    models, scores = {}, {}
    for _name in mo.status.progress_bar(candidates.value, title="Training"):
        models[_name] = trainer.CANDIDATES[_name]().fit(x_train, y_train)
        scores[_name] = models[_name].predict_proba(x_valid)[:, 1]
    best = max(scores, key=lambda n: trainer.ranking_metrics(y_valid, scores[n])["pr_auc"])
    return best, models, scores, y_valid


@app.cell
def _(best, scores, y_valid):
    curves = []
    for _name, _proba in scores.items():
        _precision, _recall, _ = precision_recall_curve(y_valid, _proba)
        _keep = np.linspace(0, len(_recall) - 1, min(400, len(_recall))).astype(int)
        curves.append(pd.DataFrame({"model": _name, "recall": _recall[_keep], "precision": _precision[_keep]}))
    pr_chart = alt.Chart(pd.concat(curves)).mark_line().encode(
        x=alt.X("recall:Q", title="Recall"), y=alt.Y("precision:Q", title="Precision"), color="model:N",
    ).properties(height=280, title="Precision-recall on validation")
    table = pd.DataFrame([{"model": n, **trainer.ranking_metrics(y_valid, p)} for n, p in scores.items()])
    mo.vstack([mo.md(f"**Best on validation: `{best}`** (highest PR-AUC)."),
               mo.hstack([pr_chart, mo.ui.table(table.round(4), selection=None)], widths=[3, 2])])
    return


@app.cell
def _(best, scores, y_valid):
    review_at, block_at = trainer.choose_thresholds(y_valid, scores[best])
    threshold = mo.ui.slider(0.01, 0.99, step=0.01, value=round(review_at, 2), show_value=True,
                             label="Decision threshold")
    mo.vstack([
        mo.md(f"## Thresholds\nThe pipeline picks **review at {review_at:.3f}** (maximises F2, favouring recall) "
              f"and **block at {block_at:.3f}** (the lowest score with ≥97% precision). Explore others:"),
        threshold,
    ])
    return (threshold,)


@app.cell
def _(best, models, test, threshold):
    x_test, y_test = trainer.xy(test)
    test_proba = models[best].predict_proba(x_test)[:, 1]
    at = trainer.metrics_at(y_test, test_proba, threshold.value)
    matrix = pd.DataFrame(confusion_matrix(y_test, test_proba >= threshold.value),
                          index=["actual legit", "actual fraud"], columns=["predicted legit", "predicted fraud"])
    mo.hstack([
        mo.stat(f"{at['precision']:.1%}", label="Precision on test"),
        mo.stat(f"{at['recall']:.1%}", label="Recall on test"),
        mo.ui.table(matrix.reset_index(names=""), selection=None),
    ], widths=[1, 1, 2])
    return x_test, y_test


@app.cell
def _():
    feature = mo.ui.dropdown(options=list(MODEL_FEATURES), value="drain_ratio", label="Feature")
    mo.vstack([mo.md("## Features: fraud vs legitimate"), feature])
    return (feature,)


@app.cell
def _(feature, frame):
    values = frame[feature.value].astype(float)
    edges = np.histogram_bin_edges(values.dropna(), bins=40)
    hist = pd.concat([
        pd.DataFrame({"label": label, "bin": edges[:-1],
                      "share": np.histogram(values[frame.is_fraud == flag].dropna(), bins=edges)[0]
                      / max(int((frame.is_fraud == flag).sum()), 1)})
        for label, flag in (("legit", 0), ("fraud", 1))
    ])
    alt.Chart(hist).mark_bar(opacity=0.6).encode(
        x=alt.X("bin:Q", title=feature.value), y=alt.Y("share:Q", title="Share of class", stack=None),
        color=alt.Color("label:N", scale=alt.Scale(domain=["legit", "fraud"], range=["#1f6f50", "#b3402a"])),
    ).properties(height=240)
    return


@app.cell
def _(best, models, x_test, y_test):
    rows = x_test.sample(n=min(20_000, len(x_test)), random_state=trainer.RANDOM_STATE)
    importance = permutation_importance(models[best], rows, y_test[x_test.index.get_indexer(rows.index)],
                                        scoring="average_precision", n_repeats=3, random_state=trainer.RANDOM_STATE)
    ranked = pd.DataFrame({"feature": rows.columns, "importance": importance.importances_mean}).sort_values(
        "importance", ascending=False)
    mo.vstack([
        mo.md(f"## What `{best}` relies on\nDrop in test PR-AUC when each feature is shuffled."),
        alt.Chart(ranked).mark_bar().encode(x=alt.X("importance:Q", title="PR-AUC drop"),
                                            y=alt.Y("feature:N", sort="-x", title=None)).properties(height=320),
    ])
    return


if __name__ == "__main__":
    app.run()
