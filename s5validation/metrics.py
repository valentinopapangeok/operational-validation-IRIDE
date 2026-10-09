"""Metrics for already quality-filtered, collocated pairs. No implicit filtering."""
import math


def _pairs(predicted, reference):
    p, r = list(predicted), list(reference)
    if not p or len(p) != len(r):
        raise ValueError('Expected equal-length, non-empty paired samples')
    if any(not math.isfinite(x) for x in p + r):
        raise ValueError('Pairs must be finite; filter nodata before computing metrics')
    return p, r


def continuous(predicted, reference, denominator=None):
    p, r = _pairs(predicted, reference)
    n = len(p)
    errors = [a - b for a, b in zip(p, r)]
    mse = sum(x*x for x in errors) / n
    mean = sum(r) / n
    variance = sum((x-mean)**2 for x in r)
    result = {'n': n, 'rmse': math.sqrt(mse),
              'mae': sum(abs(x) for x in errors)/n,
              'bias': sum(errors)/n,
              # Predictive R², not squared correlation; may be negative.
              'r2': 1-sum(x*x for x in errors)/variance if variance else None,
              'relative_rmse_pct': None}
    if denominator is not None:
        if not math.isfinite(denominator) or denominator <= 0:
            raise ValueError('Relative RMSE denominator must be finite and positive')
        result['relative_rmse_pct'] = 100*result['rmse']/denominator
    return result


def binary(predicted, reference):
    p, r = _pairs(predicted, reference)
    if any(x not in (0, 1) for x in p+r):
        raise ValueError('Binary metrics require explicit 0/1 class mapping')
    tp = sum(a == 1 and b == 1 for a,b in zip(p,r))
    fp = sum(a == 1 and b == 0 for a,b in zip(p,r))
    fn = sum(a == 0 and b == 1 for a,b in zip(p,r))
    tn = sum(a == 0 and b == 0 for a,b in zip(p,r))
    ratio = lambda a,b: a/b if b else None
    return {'n':len(p), 'tp':tp, 'fp':fp, 'fn':fn, 'tn':tn,
            'accuracy':(tp+tn)/len(p), 'precision':ratio(tp,tp+fp),
            'recall_pod':ratio(tp,tp+fn), 'f1':ratio(2*tp,2*tp+fp+fn),
            'iou':ratio(tp,tp+fp+fn), 'false_alarm_ratio':ratio(fp,tp+fp)}


def precipitation(predicted, reference, threshold, denominator=None):
    p,r = _pairs(predicted, reference)
    if threshold is None or not math.isfinite(threshold) or threshold <= 0:
        raise ValueError('An explicit positive rain threshold is required')
    if any(x < 0 for x in p+r):
        raise ValueError('Rain quantities must be nonnegative after QA')
    wet = [(a,b) for a,b in zip(p,r) if a >= threshold and b >= threshold]
    return {'all_pairs':continuous(p,r,denominator),
            'event_detection':binary([int(x >= threshold) for x in p],
                                     [int(x >= threshold) for x in r]),
            'both_wet':continuous(*zip(*wet), denominator=denominator) if wet else None}


def positional_accuracy(distances_m, tolerance_m):
    """Percentage within a supplied tolerance; no tolerance inferred from OSVP."""
    values=list(distances_m)
    if tolerance_m is None or not math.isfinite(tolerance_m) or tolerance_m < 0:
        raise ValueError('An explicit nonnegative spatial tolerance in metres is required')
    if not values or any(not math.isfinite(x) or x < 0 for x in values):
        raise ValueError('Expected nonempty finite nonnegative positional distances')
    return {'n':len(values),'tolerance_m':tolerance_m,
            'positional_accuracy_pct':100*sum(x <= tolerance_m for x in values)/len(values)}


def evaluate_target(value, operator, threshold):
    """Evaluate one defined metric; unavailable values never become PASS."""
    if value is None:
        return 'UNAVAILABLE'
    if not math.isfinite(value) or not math.isfinite(threshold):
        raise ValueError('Target values must be finite')
    comparisons={'<=':lambda a,b:a <= b,'>=':lambda a,b:a >= b,'>':lambda a,b:a > b,'<':lambda a,b:a < b}
    if operator not in comparisons:
        raise ValueError('Unsupported target comparison')
    return 'PASS' if comparisons[operator](value,threshold) else 'FAIL'
