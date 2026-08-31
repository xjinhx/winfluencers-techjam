| component | HR@10 | MRR | MTTC | TechnicalScore | delta |
|---|---|---|---|---|---|
| full system | 1.000 | 0.9025 | 2.40 | 0.9427 | +0.0000 |
| - dense route (Route B) | 0.995 | 0.9027 | 2.46 | 0.9391 | -0.0036 |
| - phrase / bigram evidence | 0.995 | 0.8841 | 2.46 | 0.9335 | -0.0092 |
| - popularity priors | 0.960 | 0.7556 | 2.92 | 0.8683 | -0.0744 |
| - constraint scoring (Route C) | 0.995 | 0.8928 | 2.43 | 0.9368 | -0.0060 |
| - clarification policy | 0.630 | 0.4598 | 5.97 | 0.5535 | -0.3892 |
| - coverage + category focus | 1.000 | 0.8780 | 2.29 | 0.9375 | -0.0052 |
| - profile personalisation | 1.000 | 0.9072 | 2.42 | 0.9438 | +0.0010 |
| - per-field weighting | 0.990 | 0.8789 | 2.44 | 0.9300 | -0.0128 |
| candidate depth 200 -> 20 | 0.955 | 0.8648 | 2.83 | 0.9003 | -0.0424 |
| + MMR diversity (browsing) | 1.000 | 0.9025 | 2.40 | 0.9427 | +0.0000 |
| - recommendation hold (both gates) | 1.000 | 0.7561 | 1.89 | 0.9091 | -0.0336 |
| - span conjunction (span_all) | 0.990 | 0.8522 | 2.56 | 0.9194 | -0.0234 |
| - constraint commonness penalty | 1.000 | 0.9058 | 2.42 | 0.9434 | +0.0007 |
| - low-coverage penalties | 1.000 | 0.8990 | 2.40 | 0.9417 | -0.0010 |
| + re-ask disclosed attributes | 1.000 | 0.9025 | 2.39 | 0.9429 | +0.0002 |
