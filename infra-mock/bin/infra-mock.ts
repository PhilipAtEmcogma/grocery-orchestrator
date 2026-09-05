#!/usr/bin/env node
// import 'source-map-support/register';  // <-- remove or comment out this line
import * as cdk from 'aws-cdk-lib';
import { InfraMockStack } from '../lib/infra-mock-stack';

const app = new cdk.App();

new InfraMockStack(app, 'GroceryMockStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: 'ap-southeast-2',
  },
  description: 'Mock grocery assistant backend (Lambda + API Gateway + DynamoDB)',
});