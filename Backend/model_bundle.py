class CalibratedModelBundle:
    """Prediction wrapper persisted by the calibrated training script."""

    def __init__(
        self, predictor, explanation_model, feature_columns, category_levels,
        clip_bounds=None, approve_threshold=0.08, decline_threshold=0.20,
    ):
        self.predictor = predictor
        self.explanation_model = explanation_model
        self.feature_columns = feature_columns
        self.category_levels = category_levels
        self.clip_bounds = clip_bounds or {}
        self.approve_threshold = approve_threshold
        self.decline_threshold = decline_threshold

    def predict_proba(self, data):
        return self.predictor.predict_proba(data)
