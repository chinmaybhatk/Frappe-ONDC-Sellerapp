# IGM - Issue & Grievance Management

Handles complaints, returns, refund disputes, and escalations between BAP and BPP.

## Responsibilities

- Issue creation and tracking
- Resolution workflow management
- Escalation to higher authorities
- Refund processing coordination
- Return and replacement management

## API Endpoints

| API | Direction | Description |
|-----|-----------|-------------|
| `POST /issue` | BAP -> BPP | Raise an issue/complaint |
| `POST /on_issue` | BPP -> BAP | Respond to issue |
| `POST /issue_status` | BAP -> BPP | Check issue status |
| `POST /on_issue_status` | BPP -> BAP | Provide issue status update |

## Issue Categories

| Category | Description |
|----------|-------------|
| ITEM | Product-related issues (damaged, wrong item, quality) |
| FULFILLMENT | Delivery issues (delayed, not delivered, wrong address) |
| ORDER | Order-level issues (partial order, missing items) |
| PAYMENT | Payment issues (overcharged, refund pending) |

## Module Structure

```
src/
├── api/             # IGM API handlers
│   ├── issue.js
│   ├── on_issue.js
│   ├── issue_status.js
│   └── on_issue_status.js
├── services/        # Business logic
│   ├── issue-service.js
│   ├── resolution-service.js
│   ├── escalation-service.js
│   └── refund-service.js
├── controllers/     # HTTP controllers
├── models/          # Data models
│   ├── issue.js
│   └── resolution.js
└── config/          # Configuration
```

## Getting Started

```bash
cd igm
npm install
npm run dev
```
