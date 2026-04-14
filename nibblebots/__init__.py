"""
nibblebots — Automated research and monitoring bots for niblit-lean-algos.

Each bot runs as a scheduled GitHub Actions workflow and publishes its
findings as a GitHub Issue.  Bots never commit or push code.

Available bots
--------------
ai_trading_bot          Weekly study of top AI trading repos → improvement issue.
deployment_bot          Monitors failed workflow runs → fix suggestions issue.
improvement_bot         Weekly improvement scanner (LEAN/QC topics).
research_bot            Friday live research → synthesis issue.
aios_architecture_bot   Wednesday architecture proposals issue.
aios_integration_bot    Thursday integration roadmap issue.
aios_research_bot       Tuesday AI/LEAN research scan issue.
llm_engineer_bot        Thursday LLM-in-trading research issue.
"""
