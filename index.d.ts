/**
 * W1™ NEXUS TypeScript Definitions
 * Autonomous Intelligence Discovery & Verification Protocol
 * Licensed under MPL-2.0
 */

export const VERSION: string;
export const PROTOCOL_VERSION: string;

export interface GoalContract {
  contract_id: string;
  intent: string;
  criteria: string[];
  constraints?: string[];
  created_at?: string;
}

export interface TeamPlan {
  plan_id: string;
  goal_contract_ref: string;
  roles: {
    planner: string;
    specialist: string;
    auditor: string;
  };
  stages: string[];
}

export interface FinalResult {
  result_id: string;
  goal_contract_ref: string;
  attestation_hash: string;
  score: number;
  status: 'CERTIFIED' | 'FAILED' | 'INCONCLUSIVE';
}

export function getAvailableSchemas(): string[];
export function loadSchema(name: string): Record<string, any>;
