/**
 * `loadConfig` decides two things that used to be decided twice, differently.
 *
 * 1. WHAT COUNTS AS PRODUCTION. This file held `stage === 'prod'` while
 *    `src/handler.py` held `{prod, production, pilot}`. Both implement Req
 *    12.5's fail-closed rule; they disagreed about the set it applies to, so
 *    `stage=pilot` synthesised happily with wildcard CORS and then failed at
 *    Lambda startup. The EARLIER and cheaper guard was the one that did not
 *    fire, which is the wrong way round. Both now read `config/stages.json`.
 *
 * 2. WHAT THE ADOPTED TABLES ARE CALLED. The physical names were derived from
 *    the stage, so `stage=prod` referenced `grocery-products-prod` -- a table
 *    that does not exist. The first real production synth would have adopted
 *    nothing and granted access to nothing, and it would have deployed clean.
 *    Adoption points at something already there, so the name is an input on its
 *    own axis (`DATA_SUFFIX`), not a function of the stage.
 *
 * `tests/test_production_config.py` asserts the same file from the Python side.
 * Two readers, one list; neither can drift without the other's test failing.
 */
import * as fs from 'fs';
import * as path from 'path';
import { loadConfig, productionStages } from '../lib/config';

const STAGES_FILE = path.resolve(__dirname, '..', '..', 'config', 'stages.json');

describe('production stage definition', () => {
  it('is read from config/stages.json, not written here', () => {
    const declared: string[] = JSON.parse(fs.readFileSync(STAGES_FILE, 'utf-8')).production_stages;
    expect([...productionStages()].sort()).toEqual([...declared].map((s) => s.toLowerCase()).sort());
  });

  it('treats every declared stage as production, `pilot` included', () => {
    for (const stage of productionStages()) {
      // Each must be rejected for wildcard CORS -- the synth-time half of the
      // same rule the handler enforces at startup.
      expect(() => loadConfig(stage)).toThrow(/wildcard CORS/i);
    }
  });

  it('leaves dev permissive, because the fallbacks exist so the graph runs on a laptop', () => {
    const cfg = loadConfig('dev');
    expect(cfg.isProduction).toBe(false);
    expect(cfg.corsOrigin).toBe('*');
  });

  it('refuses a production synth with a DRAFT guardrail version', () => {
    const prior = { cors: process.env.CORS_ORIGIN, ver: process.env.BEDROCK_GUARDRAIL_VERSION };
    try {
      process.env.CORS_ORIGIN = 'https://example.test';
      process.env.BEDROCK_GUARDRAIL_VERSION = 'DRAFT';
      expect(() => loadConfig('prod')).toThrow(/NUMBERED/i);
    } finally {
      prior.cors === undefined ? delete process.env.CORS_ORIGIN : (process.env.CORS_ORIGIN = prior.cors);
      prior.ver === undefined
        ? delete process.env.BEDROCK_GUARDRAIL_VERSION
        : (process.env.BEDROCK_GUARDRAIL_VERSION = prior.ver);
    }
  });
});

describe('adopted table names are not on the stage axis', () => {
  it('a production stage still adopts the tables that exist', () => {
    const prior = process.env.CORS_ORIGIN;
    try {
      process.env.CORS_ORIGIN = 'https://example.test';
      const cfg = loadConfig('prod');
      // The tables holding 2,759 real rows are `-dev`, whatever the stage is.
      expect(cfg.names.productsTable).toBe('grocery-products-dev');
      expect(cfg.names.idempotencyTable).toBe('grocery-idempotency-dev');
      // What the stack CREATES is still stage-named, which is correct: those
      // are new resources, and two environments must not collide.
      expect(cfg.names.orchestratorFn).toBe('grocery-orchestrator-prod');
    } finally {
      prior === undefined ? delete process.env.CORS_ORIGIN : (process.env.CORS_ORIGIN = prior);
    }
  });

  it('DATA_SUFFIX moves the adopted names and nothing else', () => {
    const prior = process.env.DATA_SUFFIX;
    try {
      process.env.DATA_SUFFIX = 'staging';
      const cfg = loadConfig('dev');
      expect(cfg.names.productsTable).toBe('grocery-products-staging');
      expect(cfg.names.orchestratorFn).toBe('grocery-orchestrator-dev');
    } finally {
      prior === undefined ? delete process.env.DATA_SUFFIX : (process.env.DATA_SUFFIX = prior);
    }
  });
});
