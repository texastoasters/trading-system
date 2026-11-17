# Domain language

Terminology for research-only prospective investment evaluation, distinct from broker execution.

## Terms

### Evaluation campaign
A pre-registered series of decision checkpoints governed by one frozen evaluation protocol. Changing the protocol creates a different campaign rather than rewriting observed history.

### Arm
A named decision policy or model configuration compared with other arms using the same checkpoint observations and outcome windows. An arm is not a broker account.

### Decision checkpoint
A dated set of shared observations and arm decisions frozen before the outcomes being measured. An evidence collection supplies observations; a decision run supplies an arm's proposal or failure.

### Decision deadline
The latest admissible completion time for an arm's decision at a checkpoint. It is not a price observation or execution time.

### Price anchor
A predeclared future price observation used as the reference for an outcome. An anchor proxy is not a broker fill and does not establish that a trade was executable at that price.

### Outcome horizon
The predeclared market period over which a checkpoint is assessed. A market period and the time its data becomes available are different facts.

### Outcome observation
A subsequently received source observation used to assess a checkpoint, retaining both its market-time meaning and actual availability.

### Raw price response
A change between unadjusted price observations. It is not split/dividend-adjusted total return, realized profit or an execution-quality measurement.

### Mature outcome
An outcome whose declared market horizon has elapsed. Maturity does not imply that the required data is available or scorable.

### Scorable outcome
An outcome with sufficient valid observations under the frozen measurement rules. Scorable does not mean profitable, statistically conclusive or approved for promotion.

### Pending outcome
An outcome still waiting for its horizon or required observations. Known source failures remain distinguishable from ordinary waiting.

### Cohort measurement
An assessment of allocations from one checkpoint over a fixed horizon. Overlapping cohorts are not independent samples and cannot be multiplied into a portfolio equity curve.

### Promotion
A separately authorized move from research to a more operational mode after predeclared evidence and reliability gates. A successful smoke test is not promotion.
