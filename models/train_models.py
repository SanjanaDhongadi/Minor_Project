"""
models/train_models.py

Trains three Random Forest classifiers and saves them as .pkl files:
  1. pod_health_model.pkl      → predict pod state (Normal/Warning/SLA_Violation/Failed)
  2. recovery_action_model.pkl → predict best recovery action
  3. autoscale_model.pkl       → predict scale_up / scale_down / no_change

Run:
    python models/train_models.py
"""

import pandas as pd
import numpy as np
import pickle
from pathlib import Path

from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.preprocessing import LabelEncoder
from sklearn.metrics import classification_report, accuracy_score

DATASET_DIR = Path(__file__).parent.parent / "dataset"
MODEL_DIR   = Path(__file__).parent
MODEL_DIR.mkdir(exist_ok=True)


def train_and_save(
    df: pd.DataFrame,
    feature_cols: list[str],
    label_col: str,
    model_name: str,
    model_type: str = "rf"
):
    """Train a classifier, print metrics, and save to disk."""
    print(f"\n{'='*55}")
    print(f"  Training: {model_name}")
    print(f"{'='*55}")

    X = df[feature_cols].copy()
    y = df[label_col].copy()

    # Encode string labels
    le = LabelEncoder()
    y_enc = le.fit_transform(y)

    X_train, X_test, y_train, y_test = train_test_split(
        X, y_enc, test_size=0.2, random_state=42, stratify=y_enc
    )

    if model_type == "gb":
        clf = GradientBoostingClassifier(
            n_estimators=150, max_depth=5, learning_rate=0.1, random_state=42
        )
    else:
        clf = RandomForestClassifier(
            n_estimators=200, max_depth=None, min_samples_split=4,
            random_state=42, n_jobs=-1
        )

    clf.fit(X_train, y_train)

    # Evaluation
    y_pred = clf.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    print(f"\n  Test Accuracy: {acc:.4f} ({acc*100:.1f}%)")

    cv_scores = cross_val_score(clf, X, y_enc, cv=5, scoring="accuracy")
    print(f"  Cross-val (5-fold): {cv_scores.mean():.4f} ± {cv_scores.std():.4f}")

    print(f"\n  Classification Report:")
    print(classification_report(
        y_test, y_pred,
        target_names=le.classes_,
        zero_division=0
    ))

    # Feature importance
    importances = clf.feature_importances_
    feat_imp = sorted(zip(feature_cols, importances), key=lambda x: x[1], reverse=True)
    print("  Top feature importances:")
    for feat, imp in feat_imp[:5]:
        bar = "█" * int(imp * 40)
        print(f"    {feat:<28} {imp:.4f}  {bar}")

    # Save model + label encoder together
    bundle = {"model": clf, "label_encoder": le, "features": feature_cols}
    save_path = MODEL_DIR / f"{model_name}.pkl"
    with open(save_path, "wb") as f:
        pickle.dump(bundle, f)
    print(f"\n  Saved → {save_path}")

    return clf, le


def main():
    print("\nSLA Monitor — ML Model Trainer")
    print("================================\n")

    # ── Model 1: Pod Health Classifier ────────────────────────────────────────
    df_health = pd.read_csv(DATASET_DIR / "pod_health_dataset.csv")
    health_features = [
        "cpu_percent", "memory_percent", "restart_count",
        "response_time_ms", "error_rate_percent", "pod_phase_encoded",
        "container_ready", "oom_killed", "network_errors", "disk_pressure"
    ]
    train_and_save(
        df_health, health_features, "pod_state_label",
        model_name="pod_health_model", model_type="rf"
    )

    # ── Model 2: Recovery Action Classifier ───────────────────────────────────
    df_recovery = pd.read_csv(DATASET_DIR / "recovery_action_dataset.csv")
    recovery_features = [
        "failure_type_encoded", "cpu_percent", "memory_percent",
        "restart_count", "uptime_percent", "replica_count", "error_rate_percent"
    ]
    train_and_save(
        df_recovery, recovery_features, "recovery_action",
        model_name="recovery_action_model", model_type="rf"
    )

    # ── Model 3: Autoscaling Classifier ──────────────────────────────────────
    df_scale = pd.read_csv(DATASET_DIR / "autoscale_dataset.csv")
    scale_features = [
        "cpu_percent", "memory_percent", "requests_per_sec",
        "current_replicas", "response_time_ms", "queue_depth"
    ]
    train_and_save(
        df_scale, scale_features, "scale_action",
        model_name="autoscale_model", model_type="gb"
    )

    print("\n\nAll models trained and saved to models/")
    print("Models ready for use by agents.\n")


if __name__ == "__main__":
    main()
