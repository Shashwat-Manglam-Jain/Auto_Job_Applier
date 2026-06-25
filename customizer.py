import re
import os
import copy
import json
import time
import logging
import httpx
from resume_templates import get_template, generate_pdf_from_template
from config import PROFILE, GROQ_API_KEYS, GEMINI_API_KEYS

logger = logging.getLogger(__name__)

_groq_key_idx = 0
_gemini_key_idx = 0
_groq_last_calls: dict[int, float] = {}
_gemini_last_calls: dict[int, float] = {}


def _call_groq(prompt: str, max_tokens: int = 300, temperature: float = 0.3) -> str | None:
    global _groq_key_idx
    if not GROQ_API_KEYS:
        return None
    for attempt in range(len(GROQ_API_KEYS)):
        idx = (_groq_key_idx + attempt) % len(GROQ_API_KEYS)
        key = GROQ_API_KEYS[idx]
        last = _groq_last_calls.get(idx, 0.0)
        elapsed = time.time() - last
        if elapsed < 2.5:
            time.sleep(2.5 - elapsed)
        _groq_last_calls[idx] = time.time()
        try:
            resp = httpx.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                timeout=15,
            )
            if resp.status_code == 200:
                _groq_key_idx = (idx + 1) % len(GROQ_API_KEYS)
                return resp.json()["choices"][0]["message"]["content"].strip()
            if resp.status_code == 429:
                logger.debug("Groq key #%d rate limited, rotating to next", idx + 1)
                continue
            logger.debug("Groq key #%d returned %d", idx + 1, resp.status_code)
        except Exception as e:
            logger.debug("Groq key #%d failed: %s", idx + 1, e)
    return None


def _call_gemini(prompt: str, max_tokens: int = 300) -> str | None:
    global _gemini_key_idx
    if not GEMINI_API_KEYS:
        return None
    for attempt in range(len(GEMINI_API_KEYS)):
        idx = (_gemini_key_idx + attempt) % len(GEMINI_API_KEYS)
        key = GEMINI_API_KEYS[idx]
        last = _gemini_last_calls.get(idx, 0.0)
        elapsed = time.time() - last
        if elapsed < 2.5:
            time.sleep(2.5 - elapsed)
        _gemini_last_calls[idx] = time.time()
        try:
            resp = httpx.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}",
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.3},
                },
                timeout=15,
            )
            if resp.status_code == 200:
                _gemini_key_idx = (idx + 1) % len(GEMINI_API_KEYS)
                return (resp.json()
                        .get("candidates", [{}])[0]
                        .get("content", {})
                        .get("parts", [{}])[0]
                        .get("text", "").strip())
            if resp.status_code == 429:
                logger.debug("Gemini key #%d rate limited, rotating to next", idx + 1)
                continue
            logger.debug("Gemini key #%d returned %d", idx + 1, resp.status_code)
        except Exception as e:
            logger.debug("Gemini key #%d failed: %s", idx + 1, e)
    return None


def _call_ai(prompt: str, max_tokens: int = 300, temperature: float = 0.3) -> str | None:
    """Try all Groq keys first, then all Gemini keys, then give up."""
    result = _call_groq(prompt, max_tokens, temperature)
    if result:
        return result
    logger.info("All Groq keys failed, trying Gemini fallback...")
    return _call_gemini(prompt, max_tokens)


def _extract_keywords(text: str) -> set[str]:
    text = text.lower()
    words = re.findall(r'[a-z][a-z0-9.#+]+', text)
    bigrams = []
    word_list = text.split()
    for i in range(len(word_list) - 1):
        bigrams.append(f"{word_list[i]} {word_list[i+1]}")
    return set(words) | set(bigrams)


def _reorder_skills(skills_dict: dict, job_keywords: set[str]) -> dict:
    reordered = {}
    for category, skill_list in skills_dict.items():
        scored = []
        for skill in skill_list:
            skill_lower = skill.lower()
            match_score = sum(
                1 for kw in job_keywords
                if kw in skill_lower or skill_lower in kw
            )
            scored.append((match_score, skill))
        scored.sort(key=lambda x: x[0], reverse=True)
        reordered[category] = [s[1] for s in scored]
    return reordered


def _reorder_projects(projects: list[dict], job_keywords: set[str]) -> list[dict]:
    scored = []
    for proj in projects:
        proj_text = f"{proj['name']} {proj['stack']} {' '.join(proj['bullets'])}".lower()
        match_count = sum(1 for kw in job_keywords if kw in proj_text)
        scored.append((match_count, proj))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [p[1] for p in scored]


def _enhance_summary_with_ai(template: dict, job_title: str,
                              job_tags: list[str], job_desc: str,
                              company_name: str = "") -> str | None:
    """Use AI (Groq -> Gemini) to tailor the professional summary.

    Includes company name when available and emphasizes 3+ years of
    experience with specific technologies from the job posting.
    """
    current_summary = template.get("summary", "")
    top_tags = ", ".join(job_tags[:10])

    company_context = ""
    if company_name:
        company_context = (
            f"\nThe hiring company is {company_name}. Naturally mention "
            f"enthusiasm for contributing to {company_name}'s mission "
            f"if it fits. "
        )

    prompt = (
        f"Rewrite this professional summary for a resume targeting the "
        f"\"{job_title}\" position.\n\n"
        f"Current summary: {current_summary}\n\n"
        f"Required skills: {top_tags}\n"
        f"Job description:\n{job_desc[:600]}\n"
        f"{company_context}\n"
        f"Rules:\n"
        f"- Write EXACTLY 2-3 sentences, professional and ATS-friendly\n"
        f"- MUST mention '3+ years of experience' explicitly\n"
        f"- Reference 3-4 specific technologies from the job requirements\n"
        f"- Include 1-2 measurable achievements (percentages, user counts, cost savings)\n"
        f"- Use strong action words: delivered, architected, optimized, scaled\n"
        f"- Make it specific to what THIS company/role needs, not generic\n"
        f"- Do NOT use quotes, markdown, or labels\n"
        f"Write ONLY the summary text, nothing else."
    )

    answer = _call_ai(prompt, max_tokens=250)
    if answer:
        answer = answer.strip('"').strip("'")
        # Remove any "Summary:" or "Professional Summary:" prefix the AI might add
        for prefix in ("Professional Summary:", "Summary:", "Professional summary:"):
            if answer.startswith(prefix):
                answer = answer[len(prefix):].strip()
        if 30 < len(answer) < 500:
            return answer
    return None


def _generate_tailored_projects_with_ai(template: dict, job_title: str,
                                         job_tags: list[str],
                                         job_desc: str = "") -> list[dict] | None:
    """Use AI to generate 3 projects with 3 bullets each, tailored to the job.

    Passes the FULL job description (not just tags) so the AI understands
    exactly what the company needs. Projects use the exact tech stack from
    the posting and include specific, measurable metrics in every bullet.
    """
    existing_projects = template.get("projects", [])
    top_tags = ", ".join(job_tags[:12])
    role_title = template.get("title", "")

    projects_text = ""
    for p in existing_projects:
        projects_text += (
            f"- {p['name']} ({p['stack']}): "
            f"{p['bullets'][0][:80]}...\n"
        )

    # Pass FULL job description so AI understands the actual company needs
    desc_block = job_desc[:800] if job_desc else "Not provided"

    prompt = (
        f"Generate 3 project descriptions for a {role_title} resume "
        f"targeting a \"{job_title}\" position.\n\n"
        f"FULL JOB DESCRIPTION (read carefully):\n{desc_block}\n\n"
        f"Key technologies from the job: {top_tags}\n\n"
        f"Base projects on these (same domain/style) but tailor heavily:\n"
        f"{projects_text}\n"
        f"IMPORTANT: Each project must use technologies specifically mentioned "
        f"in the job description above. Match the EXACT tech stack.\n\n"
        f"Return ONLY a valid JSON array with exactly 3 objects:\n"
        f'- "name": descriptive, specific project name (not generic)\n'
        f'- "stack": comma-separated tech using EXACTLY the job\'s technologies\n'
        f'- "bullets": array of EXACTLY 3 bullet strings. Each bullet MUST have:\n'
        f"  1. A strong action verb (Architected, Built, Designed, Implemented, Optimized)\n"
        f"  2. Specific technology from the job posting\n"
        f"  3. A concrete metric (e.g., 40% faster, 10K+ users, $200K savings, "
        f"99.9% uptime, 3x throughput, 50ms latency)\n\n"
        f"IMPORTANT: Each bullet MUST contain a specific number or percentage.\n"
        f"IMPORTANT: Return EXACTLY 3 bullets per project, not 2.\n\n"
        f'Example: [{{"name":"Intelligent Document Processing Platform",'
        f'"stack":"Python, LangChain, FastAPI, PostgreSQL",'
        f'"bullets":['
        f'"Architected RAG pipeline processing 50K+ documents daily with 94% '
        f'retrieval accuracy, reducing manual document review time by 60%",'
        f'"Built FastAPI microservice handling 5K+ RPM with sub-100ms P95 '
        f'latency, implementing rate limiting and structured logging",'
        f'"Implemented CI/CD pipeline with 92% test coverage, cutting deployment '
        f'time from 2 hours to 8 minutes and reducing post-release bugs by 45%"'
        f"]}}]\n\n"
        f"Return ONLY the JSON array. No markdown fences, no explanation, "
        f"no surrounding text."
    )

    answer = _call_ai(prompt, max_tokens=900, temperature=0.4)
    if not answer:
        return None

    return _parse_project_json(answer)


def _parse_project_json(answer: str) -> list[dict] | None:
    """Parse and validate a JSON project array from AI response text."""
    # Strip markdown code fences if present
    if "```" in answer:
        answer = re.sub(r"```\w*\n?", "", answer).strip()

    try:
        projects = json.loads(answer)
    except json.JSONDecodeError:
        # Try to extract JSON array from surrounding text
        match = re.search(r'\[.*\]', answer, re.DOTALL)
        if match:
            try:
                projects = json.loads(match.group())
            except json.JSONDecodeError:
                logger.debug("Failed to parse extracted JSON from AI response")
                return None
        else:
            logger.debug("No JSON array found in AI response")
            return None

    if not isinstance(projects, list) or len(projects) < 2:
        return None

    valid = []
    for p in projects[:3]:
        if not all(k in p for k in ("name", "stack", "bullets")):
            continue
        if not isinstance(p["bullets"], list) or len(p["bullets"]) < 2:
            continue
        # Ensure bullets are strings, keep up to 3
        p["bullets"] = [str(b) for b in p["bullets"][:3]]
        valid.append(p)

    if len(valid) >= 2:
        return valid
    return None


_INDUSTRY_PROJECTS = {
    "fintech": [
        {
            "name": "Real-Time Payment Processing Platform",
            "bullets": [
                "Architected a payment processing system handling 50K+ daily transactions with sub-200ms "
                "latency, implementing idempotency keys and distributed locking to prevent duplicate charges.",
                "Built real-time fraud detection pipeline analyzing transaction patterns across 15+ risk "
                "signals, reducing fraudulent transactions by 68% and saving $320K annually in chargebacks.",
                "Designed multi-currency settlement engine with automated reconciliation, supporting 12 "
                "payment methods across 8 countries with 99.97% uptime SLA compliance.",
            ],
        },
        {
            "name": "KYC/AML Compliance Automation System",
            "bullets": [
                "Built automated KYC verification pipeline processing 3K+ identity documents daily with "
                "OCR extraction and cross-reference validation, reducing manual review time by 75%.",
                "Implemented risk scoring engine evaluating 20+ compliance signals per customer, achieving "
                "99.2% accuracy in flagging suspicious activities while maintaining 4-second processing time.",
                "Designed audit trail and reporting system generating regulatory reports for 3 jurisdictions, "
                "reducing compliance team workload by 60% and eliminating manual data entry errors.",
            ],
        },
        {
            "name": "Investment Portfolio Analytics Dashboard",
            "bullets": [
                "Developed portfolio analytics platform tracking $50M+ in assets across 500+ positions "
                "with real-time P&L calculations, risk metrics, and benchmark comparisons.",
                "Built automated reporting engine generating daily NAV calculations and monthly investor "
                "statements, reducing report generation time from 4 hours to 8 minutes.",
                "Implemented market data integration with 5 data providers using failover routing, "
                "achieving 99.9% data availability and sub-second price update propagation.",
            ],
        },
    ],
    "ecommerce": [
        {
            "name": "Order Management & Fulfillment Engine",
            "bullets": [
                "Architected order management system processing 15K+ daily orders across 3 warehouses "
                "with real-time inventory sync, automated routing, and carrier rate optimization.",
                "Built inventory forecasting module using historical sales patterns and seasonality analysis, "
                "reducing stockouts by 42% and excess inventory costs by $180K annually.",
                "Implemented multi-channel order aggregation from marketplace and direct "
                "channels with unified tracking, reducing fulfillment errors by 55%.",
            ],
        },
        {
            "name": "Product Search & Recommendation Engine",
            "bullets": [
                "Built search engine indexing 200K+ products with faceted filtering, typo tolerance, and "
                "relevance tuning, improving search-to-purchase conversion rate by 35%.",
                "Developed collaborative filtering recommendation system analyzing purchase history of "
                "100K+ users, increasing average order value by 22% through personalized suggestions.",
                "Implemented A/B testing framework for search ranking algorithms with statistical significance "
                "tracking, enabling data-driven optimization of product discovery flows.",
            ],
        },
        {
            "name": "Dynamic Pricing & Promotions Platform",
            "bullets": [
                "Designed rule-based pricing engine supporting volume discounts, bundle pricing, and "
                "time-limited promotions across 50K+ SKUs with sub-50ms price calculation latency.",
                "Built promotional campaign management system with targeting rules, budget caps, and "
                "real-time analytics, handling 200+ concurrent campaigns during peak sale events.",
                "Implemented price change audit system with approval workflows and impact analysis, "
                "preventing pricing errors that previously cost $45K/quarter in revenue leakage.",
            ],
        },
    ],
    "healthcare": [
        {
            "name": "Patient Records Management System",
            "bullets": [
                "Architected HIPAA-compliant EHR system managing 200K+ patient records with role-based "
                "access control, field-level encryption, and comprehensive audit logging.",
                "Built clinical data pipeline integrating lab results, imaging reports, and pharmacy "
                "records from 8 external systems, reducing data entry by 65%.",
                "Implemented appointment scheduling engine with provider availability management, automated "
                "reminders, and waitlist optimization, reducing no-show rates by 28%.",
            ],
        },
        {
            "name": "Telemedicine Consultation Platform",
            "bullets": [
                "Developed telemedicine platform supporting 500+ daily video consultations with real-time "
                "vitals monitoring integration, e-prescriptions, and session recording for compliance.",
                "Built provider matching algorithm considering specialization, availability, patient history, "
                "and insurance coverage, reducing average wait time from 45 minutes to 8 minutes.",
                "Implemented end-to-end encryption for all patient communications with compliant "
                "storage, passing 3 consecutive security audits with zero critical findings.",
            ],
        },
        {
            "name": "Clinical Analytics & Reporting Platform",
            "bullets": [
                "Designed analytics dashboard tracking patient outcomes across 15 clinical metrics with "
                "drill-down capabilities, serving 200+ healthcare providers across 5 facilities.",
                "Built automated regulatory reporting pipeline generating compliance reports for 3 "
                "frameworks, reducing manual preparation from 40 hours to 2 hours per quarter.",
                "Implemented population health monitoring with anomaly detection, identifying 12 "
                "emerging health trends 3 weeks earlier than manual surveillance methods.",
            ],
        },
    ],
    "saas": [
        {
            "name": "Multi-Tenant SaaS Platform with Usage-Based Billing",
            "bullets": [
                "Architected multi-tenant platform serving 2K+ organizations with tenant isolation, "
                "configurable feature flags, and custom branding, supporting 50K+ concurrent users.",
                "Built usage metering and billing pipeline tracking 10M+ API calls daily with "
                "real-time quota enforcement, automated invoicing, and payment integration.",
                "Implemented tenant onboarding automation reducing setup time from 3 days to 15 minutes, "
                "including data migration tools and guided configuration wizards.",
            ],
        },
        {
            "name": "Real-Time Collaboration & Notification System",
            "bullets": [
                "Built real-time collaboration engine supporting 10K+ "
                "concurrent users with conflict resolution, presence indicators, and activity feeds.",
                "Designed multi-channel notification system delivering 100K+ daily notifications via "
                "email, push, and in-app channels with user preference management and delivery tracking.",
                "Implemented role-based access control with team hierarchies, project-level permissions, "
                "and SSO integration, reducing access-related support tickets by 40%.",
            ],
        },
        {
            "name": "Self-Service Analytics & Reporting Dashboard",
            "bullets": [
                "Developed analytics dashboard with drag-and-drop report builder, "
                "50+ pre-built widgets, and custom query support serving 500+ business users.",
                "Built report scheduling engine with PDF/CSV export, email distribution lists, and "
                "Slack integration, automating 200+ weekly reports and saving 30+ hours of manual work.",
                "Implemented data caching layer with intelligent invalidation reducing average dashboard "
                "load time from 8 seconds to 1.2 seconds for complex multi-join queries.",
            ],
        },
    ],
    "devops_infra": [
        {
            "name": "Container Orchestration & Monitoring Platform",
            "bullets": [
                "Designed deployment platform managing 150+ microservices across 3 environments "
                "with automated canary deployments, rollback triggers, and resource optimization.",
                "Built observability stack with centralized logging, distributed tracing, and custom "
                "alerting rules, reducing mean time to detection from 25 minutes to 3 minutes.",
                "Implemented infrastructure-as-code pipeline with automated drift detection, policy "
                "enforcement, and cost optimization, reducing cloud spend by 35% ($120K annually).",
            ],
        },
        {
            "name": "CI/CD Pipeline Automation Framework",
            "bullets": [
                "Architected CI/CD framework standardizing build and deployment across 40+ repositories "
                "with parallel test execution, artifact caching, and environment promotion workflows.",
                "Built self-service developer portal for environment provisioning, secret management, "
                "and deployment triggers, reducing deployment time from 2 hours to 12 minutes.",
                "Implemented security scanning integration in CI pipelines with automated "
                "vulnerability remediation, blocking 200+ critical issues before production.",
            ],
        },
        {
            "name": "Cloud Cost Optimization & Governance System",
            "bullets": [
                "Designed cloud cost monitoring platform with per-team attribution, budget alerts, "
                "and rightsizing recommendations, reducing monthly cloud spend by 40% ($200K savings).",
                "Built automated resource lifecycle management with scheduled scaling, unused resource "
                "cleanup, and reservation planning, improving utilization from 35% to 72%.",
                "Implemented compliance-as-code framework enforcing 50+ security and tagging policies "
                "across 3 cloud accounts with automated remediation and exception workflows.",
            ],
        },
    ],
    "data_platform": [
        {
            "name": "Real-Time Data Ingestion & Processing Pipeline",
            "bullets": [
                "Architected streaming data pipeline ingesting 5M+ events/day from 20+ sources with "
                "exactly-once processing semantics, schema validation, and dead-letter queue handling.",
                "Built data quality monitoring framework with automated anomaly detection, freshness "
                "checks, and lineage tracking, reducing data incidents by 72%.",
                "Implemented incremental processing with checkpoint recovery, reducing reprocessing "
                "costs by 60% and enabling sub-minute data availability for downstream consumers.",
            ],
        },
        {
            "name": "Data Warehouse & Analytics Platform",
            "bullets": [
                "Designed dimensional data warehouse with 80+ models across staging, intermediate, "
                "and mart layers, serving 200+ analysts with 99.9% query availability.",
                "Built automated data transformation pipeline with dependency management, incremental "
                "builds, and cost-aware scheduling, reducing warehouse compute costs by 45%.",
                "Implemented data catalog with column-level lineage, automated documentation, and "
                "usage analytics, improving data discovery time by 70% across the organization.",
            ],
        },
        {
            "name": "Machine Learning Feature Store",
            "bullets": [
                "Built feature store serving 50+ ML features with point-in-time correctness, "
                "feature versioning, and sub-10ms online serving latency for real-time predictions.",
                "Designed feature computation pipeline processing 2M+ records daily with backfill "
                "capability, automated drift detection, and feature importance tracking.",
                "Implemented feature sharing marketplace with discovery, access control, and quality "
                "metrics, reducing duplicate feature engineering effort by 55% across 4 ML teams.",
            ],
        },
    ],
    "ai_ml": [
        {
            "name": "Intelligent Document Processing Pipeline",
            "bullets": [
                "Architected document processing pipeline handling 10K+ documents daily with automated "
                "classification (95% accuracy), entity extraction, and structured data output.",
                "Built model training infrastructure with experiment tracking, hyperparameter tuning, "
                "and A/B model comparison, reducing model iteration cycle from 2 weeks to 3 days.",
                "Implemented model serving API with batched inference, model versioning, and canary "
                "deployment supporting 5K+ RPM with P99 latency under 200ms.",
            ],
        },
        {
            "name": "RAG-Powered Knowledge Base System",
            "bullets": [
                "Built retrieval-augmented generation system over 50K+ documents with hybrid search "
                "(semantic + keyword), achieving 92% answer relevance and citation accuracy.",
                "Designed chunking and embedding pipeline with metadata filtering, cross-encoder "
                "re-ranking, and confidence scoring, reducing support ticket volume by 40%.",
                "Implemented evaluation framework with automated regression testing across 500+ "
                "test queries, enabling safe weekly model updates without quality degradation.",
            ],
        },
        {
            "name": "Production ML Prediction Service",
            "bullets": [
                "Deployed prediction service handling 20K+ daily requests with model ensemble approach "
                "achieving 0.91 F1 score, feature importance logging, and explainability reports.",
                "Built automated retraining pipeline triggered by data drift detection, reducing model "
                "staleness from 30 days to 7 days with zero-downtime model swaps.",
                "Implemented comprehensive ML monitoring with prediction distribution tracking, feature "
                "drift alerts, and business metric correlation, preventing 12 silent model failures.",
            ],
        },
    ],
    "education": [
        {
            "name": "Adaptive Learning Management Platform",
            "bullets": [
                "Built learning platform serving 10K+ students with adaptive content sequencing, "
                "progress tracking across 200+ courses, and automated quiz generation.",
                "Implemented learning analytics dashboard tracking completion rates, engagement metrics, "
                "and knowledge gaps, enabling instructors to improve course content by 30%.",
                "Designed content delivery system with video streaming, interactive exercises, and "
                "offline access support, achieving 98.5% content availability.",
            ],
        },
        {
            "name": "Student Assessment & Grading Engine",
            "bullets": [
                "Architected automated assessment system handling 50K+ submissions/month with "
                "plagiarism detection, rubric-based grading, and instant feedback delivery.",
                "Built question bank management with difficulty calibration, topic tagging, and "
                "adaptive test generation, improving assessment coverage by 40%.",
                "Implemented grade analytics with performance trends, at-risk student identification, "
                "and intervention recommendations, reducing course failure rates by 18%.",
            ],
        },
        {
            "name": "Live Classroom & Collaboration Platform",
            "bullets": [
                "Developed virtual classroom supporting 500+ concurrent sessions with screen sharing, "
                "breakout rooms, collaborative whiteboards, and session recording.",
                "Built real-time engagement tracking with attention metrics, poll results, and "
                "participation scores, helping instructors identify and re-engage passive learners.",
                "Implemented asynchronous discussion forums with threaded replies, upvoting, and "
                "instructor highlights, increasing student participation by 55%.",
            ],
        },
    ],
    "logistics": [
        {
            "name": "Fleet Management & Route Optimization System",
            "bullets": [
                "Architected fleet management platform tracking 500+ vehicles in real-time with GPS "
                "integration, geofencing alerts, and driver performance analytics.",
                "Built route optimization engine reducing average delivery time by 25% and fuel costs "
                "by 18% through multi-stop planning with traffic and weather consideration.",
                "Implemented shipment tracking portal with real-time status updates, ETA predictions, "
                "and automated customer notifications, reducing support inquiries by 50%.",
            ],
        },
        {
            "name": "Warehouse Management & Inventory System",
            "bullets": [
                "Designed warehouse management system coordinating picking, packing, and shipping "
                "across 3 facilities, improving order fulfillment accuracy from 94% to 99.5%.",
                "Built inventory optimization engine with demand forecasting, reorder point "
                "calculations, and supplier lead time tracking, reducing carrying costs by 28%.",
                "Implemented barcode/RFID integration for real-time stock tracking with automated "
                "cycle counting, reducing inventory discrepancies by 85%.",
            ],
        },
        {
            "name": "Last-Mile Delivery Tracking Platform",
            "bullets": [
                "Developed delivery tracking platform with real-time driver location, automated "
                "dispatch assignment, and proof-of-delivery capture for 2K+ daily deliveries.",
                "Built customer-facing tracking portal with live ETA updates, delivery preferences, "
                "and feedback collection, improving customer satisfaction scores by 32%.",
                "Implemented delivery analytics dashboard with route efficiency metrics, driver "
                "performance KPIs, and SLA compliance tracking across 5 delivery zones.",
            ],
        },
    ],
    "security": [
        {
            "name": "Security Operations & Threat Detection Platform",
            "bullets": [
                "Built SIEM integration pipeline aggregating logs from 50+ sources with real-time "
                "correlation rules, reducing alert triage time from 15 minutes to 2 minutes.",
                "Implemented automated incident response playbooks for 8 common threat categories "
                "with containment actions, evidence collection, and escalation workflows.",
                "Designed vulnerability management dashboard with risk scoring, SLA tracking, and "
                "remediation workflows, reducing critical vulnerability exposure by 80%.",
            ],
        },
        {
            "name": "Identity & Access Management Platform",
            "bullets": [
                "Architected IAM platform supporting 50K+ users with SSO, MFA, and adaptive "
                "authentication, reducing unauthorized access incidents by 92%.",
                "Built automated access review system with role mining, least-privilege "
                "recommendations, and compliance reporting for SOC2/ISO27001 audits.",
                "Implemented API security gateway with token management, rate limiting, and "
                "request validation, blocking 10K+ malicious requests daily.",
            ],
        },
        {
            "name": "Compliance Monitoring & Audit System",
            "bullets": [
                "Designed continuous compliance monitoring for 3 frameworks with automated "
                "evidence collection, gap identification, and remediation tracking.",
                "Built security configuration baseline scanner checking 200+ controls across "
                "cloud infrastructure, reducing audit preparation time by 70%.",
                "Implemented data classification and DLP system scanning 5M+ documents with "
                "automated tagging, access restrictions, and leak detection alerts.",
            ],
        },
    ],
    "general": [
        {
            "name": "Enterprise API Gateway & Integration Platform",
            "bullets": [
                "Architected API gateway handling 100K+ daily requests across 30+ microservices with "
                "authentication, rate limiting, request transformation, and comprehensive logging.",
                "Built service mesh with health checks, circuit breakers, and automatic failover, "
                "achieving 99.95% uptime and reducing cascading failure incidents by 85%.",
                "Implemented API versioning strategy with backward compatibility validation, automated "
                "documentation generation, and developer portal serving 50+ external integrators.",
            ],
        },
        {
            "name": "Event-Driven Workflow Automation Engine",
            "bullets": [
                "Designed event-driven workflow engine processing 20K+ daily events with configurable "
                "trigger rules, retry policies, and dead-letter queue handling.",
                "Built workflow builder interface supporting 15+ action types with conditional branching, "
                "parallel execution, and timeout management for complex business processes.",
                "Implemented observability layer with execution tracing, performance metrics, and SLA "
                "monitoring, reducing workflow debugging time by 70%.",
            ],
        },
        {
            "name": "User Management & Access Control System",
            "bullets": [
                "Built authentication system supporting OAuth2, SAML SSO, and MFA for 50K+ users "
                "with session management, device tracking, and suspicious login detection.",
                "Implemented granular RBAC with organization hierarchies, project-level permissions, "
                "and API key management, reducing access-related support tickets by 60%.",
                "Designed user onboarding pipeline with automated provisioning, welcome workflows, "
                "and progressive profile completion, improving activation rate by 35%.",
            ],
        },
    ],
}

_INDUSTRY_KEYWORDS = {
    "fintech": {"payment", "banking", "fintech", "financial", "trading", "investment",
                "insurance", "lending", "credit", "debit", "transaction", "wallet",
                "kyc", "aml", "compliance", "forex", "crypto", "blockchain", "neobank",
                "accounting", "invoice", "billing", "stripe", "plaid", "remittance"},
    "ecommerce": {"ecommerce", "e-commerce", "marketplace", "shopping", "retail",
                  "product catalog", "checkout", "cart", "order", "inventory",
                  "fulfillment", "shipping", "shopify", "warehouse", "supply chain",
                  "pricing", "promotion", "loyalty", "customer"},
    "healthcare": {"healthcare", "health", "medical", "clinical", "patient", "hospital",
                   "pharma", "biotech", "telemedicine", "telehealth", "ehr", "emr",
                   "hipaa", "fhir", "hl7", "diagnostics", "genomics", "wellness"},
    "saas": {"saas", "multi-tenant", "subscription", "b2b", "platform", "dashboard",
             "onboarding", "tenant", "workspace", "collaboration", "crm",
             "project management", "productivity", "workflow"},
    "devops_infra": {"devops", "infrastructure", "kubernetes", "k8s", "terraform",
                     "ci/cd", "deployment", "monitoring", "observability", "sre",
                     "cloud infrastructure", "platform engineering", "helm", "argocd",
                     "jenkins", "ansible", "puppet", "reliability"},
    "data_platform": {"data engineer", "etl", "data pipeline", "data warehouse",
                      "data lake", "lakehouse", "spark", "airflow", "dbt",
                      "snowflake", "bigquery", "redshift", "databricks", "kafka",
                      "streaming", "batch processing", "data modeling", "data quality"},
    "ai_ml": {"machine learning", "deep learning", "nlp", "computer vision", "llm",
              "artificial intelligence", "model training", "inference", "mlops",
              "neural network", "pytorch", "tensorflow", "transformers", "rag",
              "langchain", "embedding", "fine-tuning", "recommendation", "prediction"},
    "education": {"education", "edtech", "learning", "course", "student", "training",
                  "lms", "e-learning", "curriculum", "assessment", "tutoring"},
    "logistics": {"logistics", "fleet", "delivery", "transportation", "shipping",
                  "warehouse", "route", "tracking", "supply chain", "dispatch"},
    "security": {"cybersecurity", "security", "soc", "siem", "threat", "vulnerability",
                 "incident response", "penetration", "compliance", "infosec", "iam",
                 "owasp", "firewall", "encryption"},
}

_TECH_DISPLAY_MAP = {
    "aws": "AWS", "gcp": "GCP", "ci/cd": "CI/CD", "api": "API",
    "rest api": "REST API", "grpc": "gRPC", "sql": "SQL", "css": "CSS",
    "html": "HTML", "graphql": "GraphQL", "nosql": "NoSQL", "sso": "SSO",
    "jwt": "JWT", "oauth": "OAuth", "rbac": "RBAC", "sdk": "SDK",
    "iac": "IaC", "sla": "SLA", "etl": "ETL", "elt": "ELT",
    "llm": "LLM", "rag": "RAG", "mlops": "MLOps", "devops": "DevOps",
    "k8s": "K8s", "saas": "SaaS", "b2b": "B2B", "ehr": "EHR",
    "node.js": "Node.js", "next.js": "Next.js", "vue.js": "Vue.js",
    "react native": "React Native", "spring boot": "Spring Boot",
    "ruby on rails": "Ruby on Rails", "scikit-learn": "Scikit-learn",
    "power bi": "Power BI", "github actions": "GitHub Actions",
    "gitlab ci": "GitLab CI",
    "fastapi": "FastAPI", "pytorch": "PyTorch", "tensorflow": "TensorFlow",
    "postgresql": "PostgreSQL", "mysql": "MySQL", "mongodb": "MongoDB",
    "dynamodb": "DynamoDB", "elasticsearch": "Elasticsearch",
    "redis": "Redis", "docker": "Docker", "kubernetes": "Kubernetes",
    "terraform": "Terraform", "ansible": "Ansible",
    "react": "React", "angular": "Angular", "vue": "Vue",
    "svelte": "Svelte", "typescript": "TypeScript", "javascript": "JavaScript",
    "python": "Python", "java": "Java", "golang": "Golang", "go": "Go",
    "rust": "Rust", "c++": "C++", "c#": "C#", "scala": "Scala",
    "kotlin": "Kotlin", "swift": "Swift", "dart": "Dart",
    "ruby": "Ruby", "php": "PHP", "laravel": "Laravel",
    "django": "Django", "flask": "Flask", "express": "Express",
    "nuxt": "Nuxt", "tailwind": "Tailwind CSS",
    "bootstrap": "Bootstrap", "sass": "Sass",
    "kafka": "Kafka", "rabbitmq": "RabbitMQ", "celery": "Celery",
    "airflow": "Airflow", "flink": "Flink",
    "langchain": "LangChain", "llamaindex": "LlamaIndex",
    "openai": "OpenAI", "huggingface": "HuggingFace",
    "prometheus": "Prometheus", "grafana": "Grafana", "datadog": "Datadog",
    "snowflake": "Snowflake", "bigquery": "BigQuery", "redshift": "Redshift",
    "databricks": "Databricks", "dbt": "dbt",
    "tableau": "Tableau", "looker": "Looker", "metabase": "Metabase",
    "figma": "Figma", "storybook": "Storybook",
    "nginx": "Nginx", "prisma": "Prisma",
    "jest": "Jest", "pytest": "pytest", "playwright": "Playwright",
    "cypress": "Cypress", "selenium": "Selenium",
    "firebase": "Firebase", "supabase": "Supabase",
    "vercel": "Vercel", "netlify": "Netlify", "heroku": "Heroku",
    "stripe": "Stripe", "twilio": "Twilio", "sendgrid": "SendGrid",
    "flutter": "Flutter", "ionic": "Ionic",
    "jenkins": "Jenkins", "circleci": "CircleCI",
    "new relic": "New Relic", "sentry": "Sentry",
    "pandas": "Pandas", "numpy": "NumPy",
    "websocket": "WebSocket", "material ui": "Material UI",
    "sqlalchemy": "SQLAlchemy", "typeorm": "TypeORM", "drizzle": "Drizzle",
    "sequelize": "Sequelize",
}

_KNOWN_TECH = [
    "react", "angular", "vue", "svelte", "next.js", "nuxt",
    "node.js", "express", "fastapi", "django", "flask", "spring boot",
    "python", "javascript", "typescript", "java", "golang", "go", "rust", "c++", "c#",
    "ruby", "ruby on rails", "php", "laravel", "scala", "kotlin", "swift", "dart",
    "postgresql", "mysql", "mongodb", "redis", "elasticsearch", "cassandra", "dynamodb",
    "sqlite", "oracle", "sql server",
    "docker", "kubernetes", "terraform", "aws", "gcp", "azure",
    "kafka", "rabbitmq", "celery", "airflow", "flink",
    "pytorch", "tensorflow", "scikit-learn", "pandas", "numpy",
    "langchain", "llamaindex", "openai", "huggingface",
    "graphql", "rest api", "grpc", "websocket",
    "tailwind", "bootstrap", "sass", "material ui",
    "jenkins", "github actions", "gitlab ci", "ci/cd", "circleci",
    "prometheus", "grafana", "datadog", "new relic", "sentry",
    "snowflake", "bigquery", "redshift", "databricks", "dbt",
    "power bi", "tableau", "looker", "metabase",
    "figma", "storybook",
    "nginx", "apache", "caddy",
    "prisma", "sequelize", "sqlalchemy", "typeorm", "drizzle",
    "jest", "pytest", "playwright", "cypress", "selenium",
    "firebase", "supabase", "vercel", "netlify", "heroku",
    "stripe", "twilio", "sendgrid",
    "react native", "flutter", "ionic",
]


def _format_tech(t: str) -> str:
    key = t.lower().strip()
    if key in _TECH_DISPLAY_MAP:
        return _TECH_DISPLAY_MAP[key]
    if key in ("aws", "gcp", "ci/cd", "api", "rest api", "grpc", "sql",
               "css", "html", "jwt", "sso", "sdk", "etl", "elt"):
        return key.upper()
    return t.strip().title()


def _detect_industry(job_desc: str, job_tags: list[str]) -> str:
    text = f"{job_desc} {' '.join(job_tags)}".lower()
    scores = {}
    for industry, keywords in _INDUSTRY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text)
        if score > 0:
            scores[industry] = score
    if not scores:
        return "general"
    return max(scores, key=scores.get)


def _extract_job_techs(job_desc: str, job_tags: list[str]) -> list[str]:
    text = f"{job_desc} {' '.join(job_tags)}".lower()
    found = []
    for tech in _KNOWN_TECH:
        if tech in text and tech not in found:
            found.append(tech)
    for tag in job_tags:
        tag_l = tag.lower().strip()
        if tag_l and tag_l not in found and len(tag_l) > 1:
            found.append(tag_l)
    return found


def _generate_fallback_projects(template: dict, job_tags: list[str],
                                 job_desc: str) -> list[dict]:
    """Generate industry-relevant projects when AI is unavailable.

    Detects the job's industry from description keywords, selects real-world
    project templates that solve problems relevant to that industry, and
    injects the exact tech stack from the job posting.
    """
    job_tags_lower = [t.lower().strip() for t in job_tags if t.strip()]
    desc_lower = (job_desc or "").lower()

    industry = _detect_industry(desc_lower, job_tags_lower)
    job_techs = _extract_job_techs(desc_lower, job_tags_lower)

    industry_pool = _INDUSTRY_PROJECTS.get(industry, [])
    general_pool = _INDUSTRY_PROJECTS["general"]

    candidates = list(industry_pool)
    if len(candidates) < 3:
        for p in general_pool:
            if p not in candidates:
                candidates.append(p)
            if len(candidates) >= 3:
                break

    existing = template.get("projects", [])
    if len(candidates) < 3 and existing:
        for p in existing:
            if len(candidates) >= 3:
                break
            candidates.append(copy.deepcopy(p))

    projects = copy.deepcopy(candidates[:3])

    if not job_techs:
        return projects

    top_techs = job_techs[:5]
    new_stack = ", ".join(_format_tech(t) for t in top_techs)

    for proj in projects:
        proj["stack"] = new_stack

    return projects


def _generate_fallback_summary(template: dict, job_title: str,
                                job_tags: list[str], company_name: str = "") -> str:
    """Manual fallback summary when AI is unavailable."""
    role_title = template.get("title", "Software Engineer")
    formatted_tags = [_format_tech(t) for t in job_tags[:5]] if job_tags else []
    top_skills = ", ".join(formatted_tags) if formatted_tags else "modern technologies"
    company_part = f" at {company_name}" if company_name else ""

    return (
        f"Results-driven {role_title} with 3+ years of experience delivering "
        f"high-impact solutions using {top_skills}. Proven ability to build "
        f"scalable systems and collaborate with cross-functional teams to drive "
        f"measurable improvements. Seeking to contribute expertise{company_part} "
        f"as a {job_title}."
    )


def generate_custom_resume(role_key: str, job_tags: list[str],
                           job_description: str, job_title: str = "",
                           company_name: str = "") -> bytes:
    """Generate a customized PDF resume tailored to a specific job posting.

    Customization pipeline:
        1. Reorder skills by relevance to the job
        2. Enhance summary: AI (Groq -> Gemini) -> manual fallback
        3. Tailor projects: AI (Groq -> Gemini) -> manual fallback
        4. Generate PDF

    Args:
        role_key: template key (e.g. 'data_scientist')
        job_tags: list of skill/technology tags from the job
        job_description: full job description text
        job_title: the job title being applied for
        company_name: name of the hiring company (used in summary)

    Returns:
        PDF file content as bytes.
    """
    template = get_template(role_key)
    if not template:
        from resume_templates import ROLE_TEMPLATES
        if isinstance(ROLE_TEMPLATES, list) and ROLE_TEMPLATES:
            template = ROLE_TEMPLATES[0]
        elif isinstance(ROLE_TEMPLATES, dict):
            template = next(iter(ROLE_TEMPLATES.values()), None)
        if not template:
            raise ValueError(f"No template found for {role_key}")

    effective_title = job_title or template.get("title", "")
    job_keywords = _extract_keywords(
        " ".join(job_tags) + " " + job_description[:500]
    )

    custom = copy.deepcopy(template)

    # Step 1: Reorder skills by relevance to job keywords
    custom["skills"] = _reorder_skills(custom["skills"], job_keywords)

    if custom.get("projects"):
        custom["projects"] = _reorder_projects(custom["projects"], job_keywords)

    # Step 2: Summary -- try AI first (Groq -> Gemini), then manual fallback
    enhanced_summary = _enhance_summary_with_ai(
        template, effective_title, job_tags,
        job_description, company_name=company_name,
    )
    if enhanced_summary:
        custom["summary"] = enhanced_summary
        logger.info("Resume summary: AI-generated for '%s'", effective_title)
    else:
        custom["summary"] = _generate_fallback_summary(
            template, effective_title, job_tags, company_name,
        )
        logger.info("Resume summary: manual fallback for '%s'", effective_title)

    # Step 3: Projects -- try AI first (Groq -> Gemini), then manual fallback
    tailored_projects = _generate_tailored_projects_with_ai(
        template, effective_title, job_tags,
        job_desc=job_description,
    )
    if tailored_projects:
        custom["projects"] = tailored_projects
        logger.info("Resume projects: AI-generated (%d projects) for '%s'",
                     len(tailored_projects), effective_title)
    else:
        fallback_projects = _generate_fallback_projects(
            template, job_tags, job_description,
        )
        if fallback_projects:
            custom["projects"] = fallback_projects
            logger.info("Resume projects: manual fallback (%d projects) for '%s'",
                         len(fallback_projects), effective_title)
        else:
            logger.info("Resume projects: kept template defaults for '%s'",
                         effective_title)

    return generate_pdf_from_template(custom, PROFILE)
