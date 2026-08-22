"""FastAPI app. Guide §5.7.

    POST   /api/runs                              start a run -> run_id
    GET    /api/runs/{id}                         status + summary metrics
    GET    /api/runs/{id}/exceptions              the hero screen's data
    GET    /api/runs/{id}/review                  review queue
    POST   /api/review/{match_id}/approve
    POST   /api/review/{match_id}/reject
    POST   /api/exceptions/{id}/actions/{action}  execute a Command
    GET    /api/runs/{id}/books                   books-closed + cash position
    POST   /api/benchmark                         run eval on a seeded dataset

POST /api/benchmark exists so the judge can press a button and watch the numbers
compute live (§8.4). It is a demo feature, not an afterthought — and it must
actually run, never read a saved JSON.
"""
