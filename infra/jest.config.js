// SCAFFOLD — Jest config for CDK assertion + snapshot tests (see test/ and
// infra/docs/02-CDK-SCAFFOLD.md §7). Wire these into CI as an `infra` job
// behind the existing `summary` gate (infra/docs/05-CICD.md).
module.exports = {
  testEnvironment: 'node',
  roots: ['<rootDir>/test'],
  testMatch: ['**/*.test.ts'],
  transform: {
    '^.+\\.tsx?$': 'ts-jest',
  },
};
