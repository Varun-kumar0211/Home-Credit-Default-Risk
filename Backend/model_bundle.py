class CalibratedModelBundle:
    """Prediction wrapper persisted by the calibrated training script."""

    def __init__(self, predictor, explanation_model, feature_columns, category_levels):
        self.predictor = predictor
        self.explanation_model = explanation_model
        self.feature_columns = feature_columns
        self.category_levels = category_levels

    def predict_proba(self, data):
        return self.predictor.predict_proba(data)
