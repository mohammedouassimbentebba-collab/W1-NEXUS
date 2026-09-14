# W1™ NEXUS

<p align="center">
  <img src="assets/nexus_hero_banner.svg" alt="W1™ NEXUS — Autonomous Intelligence Discovery &amp; Verification Protocol" width="100%"/>
</p>

<p align="center">
  <a href="https://opensource.org/licenses/MPL-2.0"><img src="https://img.shields.io/badge/License-MPL_2.0-blue.svg" alt="License: MPL-2.0"/></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"/></a>
  <a href="https://github.com/w1nexus/w1-nexus/actions/workflows/ci.yml"><img src="https://github.com/w1nexus/w1-nexus/actions/workflows/ci.yml/badge.svg" alt="CI"/></a>
  <a href="SECURITY.md"><img src="https://img.shields.io/badge/Security-Zero--Leak%20Verified-green.svg" alt="Zero-Leak Security"/></a>
</p>

**Local-First, Provider-Neutral Autonomous Intelligence Discovery, Benchmarking & Governance Fabric.**

---

### The 3-Second Hook
> **AI models shouldn't just generate text; they must contract, execute, challenge, and prove.**  
> W1™ NEXUS turns chaotic multi-agent conversations into a deterministic, verifiable state machine with zero secret leakage and unified multi-credential discovery.

---

### Architecture & Protocol State Machine

<p align="center">
  <img src="assets/w1_cip_architecture.svg" alt="W1-CIP Architecture" width="100%"/>
</p>

#### Core Capabilities:
- **Multi-Model / Multi-Credential Architecture**: Establishes $\text{Credential} \neq \text{Model}$. A single enterprise or individual credential can dynamically discover, entitle, and route up to 80+ catalog models without configuration bloat.
- **W1-CIP 3-Stage Governance**:
  - **Mode A (Raw Baseline)**: Direct unguided zero-shot completion.
  - **Mode B (Governed NEXUS Agent)**: Structured step verification, grounded intermediate validation, and tool invocation.
  - **Mode C (Multi-Model Collaborative Ensemble)**: 3-stage consensus pipeline (`Lead Planner` $\to$ `Specialist Solver` $\to$ `Independent Auditor & Critic`).
- **12-Class Failure Taxonomy**: Strictly isolates upstream provider infrastructure timeouts (`PROVIDER_TIMEOUT`) from genuine model reasoning failures (`WRONG_REASONING`).
- **9 Standardized Benchmark Suites**: Out-of-the-box evaluations across AutomationBench, OSWorld 2.0, Terminal-Bench 4.0, Terminal-Bench Science, FrontierMath Tier 4, ExploitBench, SRE-Bench, MRCR v2, and AA Intelligence Index.
- **Zero-Leak Security**: Cryptographic secret scrubbing in memory, telemetry, and on-disk JSONL provenance records.

---

### Empirical Benchmark Evaluation Matrix

<p align="center">
  <img src="assets/benchmark_matrix_chart.svg" alt="NEXUS Benchmark Matrix" width="100%"/>
</p>

#### Quickstart:
```bash
# 1. Clone repository
git clone https://github.com/w1nexus/w1-nexus.git
cd w1-nexus/w1cip_step52_autonomous_intelligence_discovery_certification

# 2. Install dependencies in editable mode
python -m venv .venv
source .venv/bin/activate  # Or: .\.venv\Scripts\Activate.ps1 on Windows
pip install -e .

# 3. Configure credentials
cp .env.example .env
# Edit .env and insert your NVIDIA_API_KEY

# 4. Run tests & empirical benchmark campaign
python -m unittest tests.test_multi_credential tests.test_benchmark_core
python run_empirical_campaign.py
```

---

## الوصف والوثائق بالعربية (Arabic Documentation)

منظومة لتنظيم تعاون نماذج الذكاء الاصطناعي والوكلاء حول هدف واحد، مع توثيق الأدلة والاعتراضات والقرارات والنتائج القابلة للتحقق. كود W1 Nexus مرخص بموجب **Mozilla Public License 2.0 (MPL 2.0)**، مع ملف `NOTICE` وسياسة علامة منفصلة لحماية الهوية وعلامة W1™ NEXUS.

لا يحاول المشروع استبدال بروتوكولات الاتصال أو الأدوات القائمة، بل يضيف طبقة حوكمة وتعاون متطورة فوق وسائل النقل والتنفيذ.

- تتفق على عقد واضح للهدف والقيود ومعايير النجاح.
- تقسّم العمل بحسب القدرات والاعتماديات.
- تميّز بين المقترحات والحقائق المقاسة والافتراضات.
- تعترض وتراجع بصورة منظمة.
- تنفّذ عبر الواجهة الأنسب: API أو MCP أو إضافة أو واجهة رسومية.
- تنتج نتيجة موحدة مع سجل يبيّن مصدر القرارات وحالة الاعتراضات.

## مكونات المنظومة

| المكوّن | الوظيفة | الحالة المخططة |
|---|---|---|
| **W1-CIP** | المعيار الأساسي لرسائل التعاون وحالات المهام | نقطة البداية |
| **W1 Action Runtime** | تنفيذ الأفعال وإدارة الصلاحيات والأقفال والتحقق | تنفيذ مرجعي محلي |
| **W1 Scientific Loop** | الفرضيات والتجارب وعدم اليقين وقابلية التكرار | منفذ في الخطوة 30 |
| **MCP + Tool Registry** | ربط الأدوات المحلية والبعيدة تحت سياسة وتدقيق موحدين | منفذ في الخطوة 26 |
| **Parallel Agent Runtime** | تشغيل وكلاء متوازٍ في Worktrees ودمج محكوم | منفذ في الخطوة 27 |
| **Secure Execution Fabric** | عزل OCI، حدود الموارد، الأسرار المؤقتة، Telemetry وAttestation | منفذ في الخطوة 28 |
| **Long-Term Memory + Knowledge Fabric** | ذاكرة محكومة بالمصدر والصلاحية والمشروع والتعارض والاسترجاع الهجين | منفذ في الخطوة 29 |
| **Multi-Model Access Fabric** | Portfolios من عدة نماذج محلية وسحابية وخاصة وتطبيقات خارجية دون خادم W1 | منفذ في الخطوة 32 |
| **W1 Nexus Workspace** | لوحة العمليات الحية للهدف والمهام والأدلة والقرارات | منفذ أساسها في الخطوة 31 |
| **Desktop Shell + Artifact Studio** | مستكشف ومحرر ومعاينات وإصدارات ومراجعة ونشر محكوم محليًا | منفذ أساسها في الخطوة 33 |
| **Universal Artifact Engine + Office Studio** | نموذج موحد للمستندات والجداول والعروض، Patches ومراجعة وتصدير محكوم | منفذ في الخطوة 34 |
| **Governed Computer Use** | إدراك سطح المكتب، screenshots، selectors، وإدخال محكوم مع قناة نص مؤقتة | منفذ حتى Step 36 |
| **Identity & Account / Credential Broker** | OS-native vault، OAuth PKCE/device flow، multi-account، refresh/revocation، ومراجع أسرار محكومة | منفذ في الخطوة 37 |
| **Native Desktop Packaging** | هوية تطبيق، deep links/file associations، service lifecycle، update integrity، ومصادر Windows packaging/signing | منفذ أساسه في الخطوة 38 |
| **Plugin & Adoption SDK** | SDK عام ثابت، Plugin API، manifests، grants، integrity locks، conformance وعزل subprocess | منفذ في الخطوة 39 |
| **Optional Collaboration Fabric** | Team tenancy ومشاركة مشاريع ومزامنة optimistic وتعارضات صريحة وSelf-hosted API مع TLS policy | منفذ في الخطوة 40 |
| **Release Hardening & External Evidence** | Threat model، fuzz/load/recovery، release gates، source manifests، وسجل نتائج benchmarks خارجية مدعوم بالأدلة | منفذ أساسه في الخطوة 41 |
| **AI Connections & Multi-Model Team Studio** | ربط مزودي AI وحساباتهم ونماذجهم وتكوين فرق solo/fallback/parallel/verify/challenge | منفذ في الخطوة 42 |
| **Live Provider Certification** | Harness صريح لاختبار auth/model discovery/runtime/streaming/tool calling مع أدلة منزوعة الأسرار وبدون ادعاء نجاح حي قبل التنفيذ | منفذ أساسه في الخطوة 44 |
| **Windows RC + Native Brand Assets** | SVG/PNG/ICO/ICNS/splash production assets، Windows version metadata وRC source gate | منفذ في الخطوة 45 |
| **Free Intelligence + Context Efficiency** | اكتشاف محلي للسعة وحفظ المحادثات مع token-bounded context | منفذ في الخطوة 48 |
| **W1 Intelligence Search Engine** | بحث Federated محلي عبر public AI catalogs/GitHub/Hugging Face بدون خادم W1 | منفذ في الخطوة 49 |

## المبدأ الحاكم

> لا يُقاس نجاح الفريق بكثرة الحوار أو اتفاق النماذج، بل بمدى تحقق النتيجة بالأدلة والاختبارات وإمكان إعادة إنتاجها.

## ما الذي سنبنيه أولًا؟

يركز الإصدار `v0.1` على نواة صغيرة قابلة للتطبيق:

1. بطاقة الوكيل وقدراته وحدوده.
2. عقد الهدف ومعايير النجاح والمحظورات.
3. خطة تشكيل الفريق ومنح السياق بحسب الحاجة.
4. رسم المهام ومراحلها وحالاتها.
5. المقترحات والأدلة والافتراضات وعمليات التحقق.
6. دورة القرار والاعتراض والمراجعة والتصعيد.
7. الموافقات البشرية.
8. سجل أحداث تسلسلي يمكن منه إعادة بناء الحالة.
9. نتيجة نهائية واحدة تبين ما تم التحقق منه وما بقي غير محسوم.

راجع:

- [رؤية المشروع](docs/VISION.ar.md)
- [نموذج الحوكمة](docs/GOVERNANCE.ar.md)
- [الدلالات التأسيسية](docs/FOUNDATIONAL-SEMANTICS.ar.md)
- [مرجع الأطراف PrincipalRef](docs/PRINCIPAL-REF.ar.md)
- [نطاق الإصدار الأول](docs/V0.1-SCOPE.ar.md)
- [مذكرة المراجعة](docs/revisions/2026-08-02-governance-review.ar.md)
- [مذكرة مراجعة الدلالات التأسيسية](docs/revisions/2026-08-02-foundational-semantics-review.ar.md)
- [مذكرة إغلاق الدلالات التأسيسية](docs/revisions/2026-08-02-foundational-closure.ar.md)
- [مذكرة تأسيس الجلسة](docs/revisions/2026-08-04-session-bootstrap.ar.md)
- [مذكرة اعتماد PrincipalRef](docs/revisions/2026-08-04-principal-ref.ar.md)
- [وثيقة EntityRef](docs/ENTITY-REF.ar.md)
- [وثيقة VersionedEntity](docs/VERSIONED-ENTITY.ar.md)
- [وثيقة RoleAssignment](docs/ROLE-ASSIGNMENT.ar.md)
- [وثيقة AgentCard](docs/AGENT-CARD.ar.md)
- [وثيقة GoalContract](docs/GOAL-CONTRACT.ar.md)
- [وثيقة TeamPlan](docs/TEAM-PLAN.ar.md)
- [وثيقة ContextGrant](docs/CONTEXT-GRANT.ar.md)
- [وثيقة Task](docs/TASK.ar.md)
- [وثيقة Contribution](docs/CONTRIBUTION.ar.md)
- [وثيقة Evidence](docs/EVIDENCE.ar.md)
- [وثيقة Verification](docs/VERIFICATION.ar.md)
- [وثيقة Challenge](docs/CHALLENGE.ar.md)
- [وثيقة Review](docs/REVIEW.ar.md)
- [وثيقة Approval](docs/APPROVAL.ar.md)
- [وثيقة Decision](docs/DECISION.ar.md)
- [وثيقة ProtocolEvent](docs/PROTOCOL-EVENT.ar.md)
- [وثيقة ExecutionResourcePlan](docs/EXECUTION-RESOURCE-PLAN.ar.md)
- [وثيقة FinalResult](docs/FINAL-RESULT.ar.md)
- [وثيقة Orchestrator Core](docs/ORCHESTRATOR-CORE.ar.md)
- [وثيقة Provider Connectors](docs/PROVIDER-CONNECTORS.ar.md)
- [وثيقة W1 Action Runtime](docs/ACTION-RUNTIME.ar.md)
- [وثيقة MCP وUnified Tool Registry](docs/MCP-AND-TOOL-REGISTRY.ar.md)
- [وثيقة التشغيل المتوازي ومنسق الدمج](docs/PARALLEL-MULTI-AGENT-RUNTIME.ar.md)
- [وثيقة Secure Execution Fabric](docs/SECURE-EXECUTION-FABRIC.ar.md)
- [وثيقة Long-Term Memory + Knowledge Fabric](docs/LONG-TERM-MEMORY-AND-KNOWLEDGE-FABRIC.ar.md)
- [وثيقة W1 Scientific Loop + Verification Lab](docs/SCIENTIFIC-LOOP-AND-VERIFICATION-LAB.ar.md)
- [وثيقة Multi-Model Access Fabric](docs/MULTI-MODEL-ACCESS-FABRIC.ar.md)
- [وثيقة Desktop Shell + Artifact Studio](docs/NATIVE-DESKTOP-SHELL-AND-ARTIFACT-STUDIO.ar.md)
- [وثيقة Universal Artifact Engine + Office Studio](docs/UNIVERSAL-ARTIFACT-ENGINE-AND-OFFICE-STUDIO.ar.md)
- [وثيقة Governed Computer Use](docs/GOVERNED-COMPUTER-USE.ar.md)
- [وثيقة Identity & Account / Credential Broker](docs/IDENTITY-ACCOUNT-CREDENTIAL-BROKER.ar.md)
- [وثيقة AI Connections & Multi-Model Team Studio](docs/AI-CONNECTIONS-MULTI-MODEL-TEAM-STUDIO.ar.md)
- [وثيقة Step 49 — W1 Intelligence Search Engine](docs/INTELLIGENCE-SEARCH-ENGINE-STEP49.ar.md)
- [وثيقة Step 50 — Verified Intelligence Activation Bridge](docs/VERIFIED-INTELLIGENCE-ACTIVATION-STEP50.ar.md)
- [التقييم التنفيذي المدقق dev42](docs/ACTUAL-CAPABILITY-EVALUATION-DEV42.ar.md)
- [هوية العلامة والإصدار](docs/BRAND-AND-RELEASE-IDENTITY.ar.md)
- [التقييم التنفيذي المدقق dev43](docs/ACTUAL-CAPABILITY-EVALUATION-DEV43.ar.md)
- [وثيقة Live Provider Certification](docs/PROVIDER-CERTIFICATION.ar.md)
- [التقييم التنفيذي المدقق dev44](docs/ACTUAL-CAPABILITY-EVALUATION-DEV44.ar.md)
- [التقييم التنفيذي المدقق dev45](docs/ACTUAL-CAPABILITY-EVALUATION-DEV45.ar.md)
- [مذكرة Step 45 — Windows RC & Brand Integration](docs/revisions/2026-08-10-windows-release-candidate-brand-integration.ar.md)
- [وثيقة Native Desktop Packaging](docs/NATIVE-DESKTOP-PACKAGING.ar.md)
- [وثيقة Plugin & Adoption SDK](docs/PLUGIN-AND-ADOPTION-SDK.ar.md)
- [وثيقة Optional Collaboration Fabric](docs/OPTIONAL-COLLABORATION-FABRIC.ar.md)
- [Threat Model](docs/THREAT-MODEL.md)
- [سياسة الـExternal Benchmarks](docs/EXTERNAL-BENCHMARKS.md)
- [سياسة الإصدار](docs/RELEASE-POLICY.md)
- [التقييم التنفيذي الفعلي dev33](docs/ACTUAL-CAPABILITY-EVALUATION-DEV33.ar.md)
- [التقييم التنفيذي الفعلي dev34](docs/ACTUAL-CAPABILITY-EVALUATION-DEV34.ar.md)
- [التقييم التنفيذي الفعلي dev36](docs/ACTUAL-CAPABILITY-EVALUATION-DEV36.ar.md)
- [التقييم التنفيذي الفعلي dev37](docs/ACTUAL-CAPABILITY-EVALUATION-DEV37.ar.md)
- [التقييم التنفيذي الفعلي dev38](docs/ACTUAL-CAPABILITY-EVALUATION-DEV38.ar.md)
- [التقييم التنفيذي الفعلي dev39](docs/ACTUAL-CAPABILITY-EVALUATION-DEV39.ar.md)
- [التقييم التنفيذي الفعلي dev40](docs/ACTUAL-CAPABILITY-EVALUATION-DEV40.ar.md)
- [التقييم التنفيذي الفعلي dev41](docs/ACTUAL-CAPABILITY-EVALUATION-DEV41.ar.md)
- [التقييم التنفيذي الفعلي](docs/ACTUAL-CAPABILITY-EVALUATION.ar.md)
- [دليل التبني المفتوح](docs/OPEN-ADOPTION-GUIDE.ar.md)
- [مذكرة اعتماد EntityRef](docs/revisions/2026-08-04-entity-ref.ar.md)
- [مذكرة اعتماد VersionedEntity](docs/revisions/2026-08-04-versioned-entity.ar.md)
- [مذكرة اعتماد RoleAssignment](docs/revisions/2026-08-04-role-assignment.ar.md)
- [مذكرة اعتماد AgentCard](docs/revisions/2026-08-04-agent-card.ar.md)
- [مذكرة اعتماد GoalContract](docs/revisions/2026-08-04-goal-contract.ar.md)
- [مذكرة اعتماد TeamPlan](docs/revisions/2026-08-04-team-plan.ar.md)
- [مذكرة اعتماد ContextGrant](docs/revisions/2026-08-04-context-grant.ar.md)
- [مذكرة اعتماد Task](docs/revisions/2026-08-05-task.ar.md)
- [مذكرة اعتماد Contribution](docs/revisions/2026-08-05-contribution.ar.md)
- [مذكرة اعتماد Evidence](docs/revisions/2026-08-05-evidence.ar.md)
- [مذكرة اعتماد Verification](docs/revisions/2026-08-05-verification.ar.md)
- [مذكرة اعتماد Challenge](docs/revisions/2026-08-05-challenge.ar.md)
- [مذكرة اعتماد Review](docs/revisions/2026-08-05-review.ar.md)
- [مذكرة اعتماد Approval](docs/revisions/2026-08-05-approval.ar.md)
- [مذكرة اعتماد Decision](docs/revisions/2026-08-05-decision.ar.md)
- [مذكرة اعتماد ProtocolEvent](docs/revisions/2026-08-05-protocol-event.ar.md)
- [مذكرة اعتماد ExecutionResourcePlan](docs/revisions/2026-08-05-execution-resource-plan.ar.md)
- [مذكرة اعتماد FinalResult](docs/revisions/2026-08-05-final-result.ar.md)
- [مذكرة اعتماد Orchestrator Core](docs/revisions/2026-08-05-orchestrator-core.ar.md)
- [مذكرة اعتماد Provider Connectors](docs/revisions/2026-08-05-provider-connectors.ar.md)
- [مذكرة اعتماد W1 Action Runtime](docs/revisions/2026-08-05-action-runtime.ar.md)
- [مذكرة اعتماد MCP وUnified Tool Registry](docs/revisions/2026-08-05-mcp-tool-registry.ar.md)
- [مذكرة اعتماد التشغيل المتوازي ومنسق الدمج](docs/revisions/2026-08-05-parallel-multi-agent-runtime.ar.md)
- [مذكرة اعتماد Secure Execution Fabric](docs/revisions/2026-08-05-secure-execution-fabric.ar.md)
- [مذكرة اعتماد Long-Term Memory + Knowledge Fabric](docs/revisions/2026-08-05-long-term-memory-knowledge-fabric.ar.md)
- [مذكرة اعتماد W1 Scientific Loop + Verification Lab](docs/revisions/2026-08-06-scientific-loop-verification-lab.ar.md)
- [مذكرة اعتماد Multi-Model Access Fabric](docs/revisions/2026-08-06-multi-model-access-fabric.ar.md)
- [مذكرة اعتماد Desktop Shell + Artifact Studio](docs/revisions/2026-08-06-desktop-artifact-studio.ar.md)
- [مذكرة اعتماد Universal Artifact Engine + Office Studio](docs/revisions/2026-08-06-universal-artifact-engine.ar.md)
- [مذكرة الخطوة 35 — Governed Computer Use](docs/revisions/2026-08-07-governed-computer-use.ar.md)
- [مذكرة الخطوة 36 — Computer Perception & Secure Input](docs/revisions/2026-08-07-computer-perception-secure-input.ar.md)
- [مذكرة الخطوة 37 — Identity & Account / Credential Broker](docs/revisions/2026-08-08-identity-account-credential-broker.ar.md)
- [مذكرة الخطوة 38 — Native Desktop Packaging](docs/revisions/2026-08-08-native-desktop-packaging.ar.md)
- [مذكرة الخطوة 39 — Plugin & Adoption SDK Stabilization](docs/revisions/2026-08-08-plugin-adoption-sdk-stabilization.ar.md)
- [مذكرة الخطوة 40 — Optional Collaboration Fabric](docs/revisions/2026-08-08-optional-collaboration-fabric.ar.md)
- [مذكرة الخطوة 42 — AI Connections & Multi-Model Team Studio](docs/revisions/2026-08-09-ai-connections-multi-model-team-studio.ar.md)
- [مذكرة الخطوة 43 — Brand & Release Identity](docs/revisions/2026-08-09-brand-release-identity.ar.md)
- [مذكرة الخطوة 41 — External Benchmarks & Release Hardening](docs/revisions/2026-08-08-release-hardening-external-benchmarks.ar.md)

## حالة المشروع

المشروع يملك الآن نواة مرجعية تشغيلية قابلة للتثبيت، مع بقاء المواصفة والواجهات قابلة للتطوير أثناء التقدم نحو W1 Nexus الكاملة.

## الشعار المؤقت

**Different models. One verified result.**


## آخر تحديث تقني

اعتمدت العقود التأسيسية وجميع كيانات دورة التعاون حتى `FinalResult`. أصبح `SessionStore` و`OrchestratorCore` وموصلات المزودين وواجهة `w1` و`W1 Action Runtime` منفذة. أضيف في الخطوة 26 عميل وخادم MCP وسجل أدوات موحد، وفي الخطوة 27 تشغيل عدة وكلاء بالتوازي داخل Git worktrees مستقلة مع دمج محكوم. وتضيف الخطوة 28 `Secure Execution Fabric`، والخطوة 29 ذاكرة طويلة المدى، والخطوة 30 دورة علمية قابلة لإعادة الإنتاج، والخطوة 31 Workspace بصرية محلية. وتضيف الخطوة 32 `Multi-Model Access Fabric`: يستطيع المستخدم أو الشركة تكوين فريق من عدة نماذج مفضلة محلية وسحابية وخاصة وتطبيقات خارجية، وتشغيل W1 محليًا أو تضمينه عبر Python SDK وLocal Control API من دون أي خادم مملوك لـW1. وتضيف الخطوة 33 Desktop Shell وArtifact Studio محليتين: مستكشف ملفات ومحرر ومعاينات وGit وTerminal محكومة وإصدارات غير قابلة لإعادة الكتابة مع مراجعة ونشر عبر Action Runtime. وتضيف الخطوة 34 Universal Artifact Engine + Office Studio: نموذجًا موحدًا للمستندات والجداول والعروض، وتعديلات JSON Pointer قابلة للمراجعة، وتصدير DOCX/XLSX/PPTX/PDF/JSON عبر نشر ثنائي مرحلي لا يخزن المحتوى في سجل العمليات. وتضيف الخطوة 35 Governed Computer Use Foundation، ثم توسعها الخطوة 36 إلى screenshots محكومة وWin32 accessibility-lite selectors وبصمات عناصر وكتابة نص ephemeral لا تدخل سجل التدقيق. وتضيف الخطوة 37 `Identity & Account / Credential Broker`: تخزين الأسرار عبر Windows Credential Manager أو macOS Keychain أو Linux Secret Service بلا fallback نصي، OAuth authorization-code + PKCE، Device Flow، refresh/revocation، تعدد الحسابات، discovery لقدرات المزود، ومراجع `w1-account:` و`w1-credential:` التي تستهلكها Provider Connectors وModel Access دون تخزين الرمز داخل ملفات المشروع. وتضيف الخطوة 38 `Native Desktop Packaging`: هوية مستقرة للتطبيق، `w1://` و`.w1nexus` بقواعد fail-closed، lifecycle لخدمة Desktop مرتبط بهوية العملية، update manifest يتحقق من HTTPS/size/SHA-256، ومصادر Windows PyInstaller/Inno Setup مع per-user associations وsigning hooks؛ ولا تدّعي الخطوة وجود Installer موقّع قبل بناء/تحقق Windows فعلي. وتضيف الخطوة 39 `Plugin & Adoption SDK Stabilization`: سطح `w1cip.sdk 1.1.0`، Plugin API 1.0، manifests fail-closed، copy-on-install مع integrity locks، grants، conformance، subprocess host، وربط managed provider plugins بـModel Access. وتضيف الخطوة 40 `Optional Collaboration Fabric`: Team tenancy وone-time invitations وProject ACLs وhash-stored access tokens وReplica event chains ومزامنة optimistic مع ConflictRecord صريح وhash-chained team audit، إضافة إلى Self-hosted HTTP API يرفض remote plaintext transport ولا يعتمد على خادم W1 مملوك لنا. وتضيف الخطوة 41 `External Benchmarks & Release Hardening`: threat model رسمي، seeded fuzz/property probes، load/restart/tamper probes، source manifests، dependency inventory، release gates وسجلًا immutable لنتائج benchmark الخارجية يمنع تحويل الاختبارات الداخلية إلى ادعاءات تنافسية بلا raw evidence. وتضيف الخطوة 42 `AI Connections & Multi-Model Team Studio`: provider catalog واتصالات متعددة الحسابات والنماذج فوق Credential Broker، واجهة Desktop للربط وبناء الفرق، وأنماط solo/fallback/parallel/verify/challenge؛ ويطبق challenge مسار producer(s) → challenger → synthesizer فعليًا دون تصويت عددي، مع قفل host لمفاتيح المزودات الرسمية. وتغلق الخطوة 45 أصول العلامة الأصلية ومسار Windows source RC: SVG/PNG/ICO/ICNS/favicon/splash مشتقة من الشعار المختار مع hashes، وربط PyInstaller/Inno Setup بالأيقونة وWindows version metadata وRC gate. الهدف التطويري الرسمي هو **W1 Nexus الكاملة**؛ لا يتوقف المسار عند MVP.

## حالة الترخيص والهوية

اعتمد المشروع **Apache License 2.0** للكود مع `NOTICE` و`BRAND-POLICY.md`. الاسم المعروض حاليًا هو **W1 Nexus™** دون ادعاء تسجيل `®` أو وجود شركة W1 قانونية. تم تثبيت Master Concept الهندسي W+1 المختار داخل `brand/reference/` ببصمة SHA-256، وأصبحت أصول SVG/PNG/ICO/ICNS/favicon/splash الإنتاجية جاهزة في Step 45 ومثبتة hashes، من دون استبدال الشعار أو إعادة تصميمه.


## Step 47 — Product UX Redesign

W1 Nexus Desktop now ships a warm-light product shell with full dark mode, Home-first AI orchestration, Adaptive/Dreamer policy controls, live portfolio flow visualization, dedicated Connections/Teams pages, and a separated Developer workspace. See `docs/PRODUCT-UX-REDESIGN-STEP47.ar.md`.

Step 48 adds safe **Free Intelligence Discovery** plus durable, token-efficient conversation context: local Ollama discovery/adoption, opt-in local gateway detection, connected-provider model discovery without inferring free entitlement, persistent local chat history, explicit summaries, and token-bounded context packs that can retrieve governed long-term memory. See `docs/FREE-INTELLIGENCE-AND-CONTEXT-EFFICIENCY-STEP48.ar.md`.

Step 49 adds **W1 Intelligence Search Engine**: a local-first federated search layer that queries selected public AI catalogs directly from the user machine, caches public candidate metadata locally, and requires manual review before third-party candidates can reach routing. It requires no W1-owned server. See `docs/INTELLIGENCE-SEARCH-ENGINE-STEP49.ar.md`.

Step 50 adds **Verified Intelligence Activation Bridge**: search results stay inert until a local evidence gate proves that an existing user-controlled provider connection can see the exact model. OpenRouter is now a first-class API-key provider; GitHub repositories and Hugging Face model repositories remain non-activatable discovery evidence. Activated free OpenRouter models enter capacity state as `third_party_free` with unknown quota and no inferred entitlement, so default routing still requires explicit third-party-free opt-in. See `docs/VERIFIED-INTELLIGENCE-ACTIVATION-STEP50.ar.md`.
