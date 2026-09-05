import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigw from 'aws-cdk-lib/aws-apigateway';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';

export class InfraMockStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Optional: DynamoDB table for products (can be empty for mock)
    const productsTable = new dynamodb.Table(this, 'ProductsTable', {
      partitionKey: { name: 'pk', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'sk', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // Lambda function with mock logic
    const mockHandler = new lambda.Function(this, 'GroceryMockHandler', {
      runtime: lambda.Runtime.NODEJS_18_X,
      code: lambda.Code.fromAsset('lambda'),
      handler: 'index.handler',
      timeout: cdk.Duration.seconds(15),
      memorySize: 256,
      environment: {
        PRODUCTS_TABLE_NAME: productsTable.tableName,
      },
    });

    // Grant Lambda access to DynamoDB (optional if you add real data later)
    productsTable.grantReadWriteData(mockHandler);

    // REST API
    const api = new apigw.RestApi(this, 'GroceryMockApi', {
      restApiName: 'GroceryMockApi',
      description: 'Mock grocery assistant API',
      defaultCorsPreflightOptions: {
        allowOrigins: apigw.Cors.ALL_ORIGINS,
        allowMethods: apigw.Cors.ALL_METHODS,
        allowHeaders: ['Content-Type', 'X-Amz-Date', 'Authorization', 'X-Api-Key'],
      },
      deployOptions: {
        stageName: 'dev',
      },
    });

    const chatResource = api.root.addResource('chat');

    chatResource.addMethod('POST', new apigw.LambdaIntegration(mockHandler), {
      methodResponses: [
        {
          statusCode: '200',
          responseParameters: {
            'method.response.header.Access-Control-Allow-Origin': true,
          },
        },
      ],
    });

    // Outputs
    new cdk.CfnOutput(this, 'ApiUrl', {
      value: api.url,
      description: 'API Gateway base URL',
      exportName: 'GroceryMockApiUrl',
    });

    new cdk.CfnOutput(this, 'ChatUrl', {
      value: `${api.url}chat`,
      description: 'Full chat endpoint URL',
      exportName: 'GroceryMockChatUrl',
    });
  }
}