/**
 * OpenAPI transformer for Orval.
 * Maps amount, total, spend, net_cash_flow fields to the Money schema ref.
 */
module.exports = (schema) => {
  if (!schema.components) {
    schema.components = {};
  }
  if (!schema.components.schemas) {
    schema.components.schemas = {};
  }

  // Ensure Money schema exists
  schema.components.schemas.Money = {
    type: 'string',
    title: 'Money',
    description: 'String-backed decimal money type',
  };

  const moneyFields = new Set(['amount', 'total', 'spend', 'net_cash_flow']);

  const transformProperties = (obj) => {
    if (!obj || typeof obj !== 'object') return;

    if (obj.properties) {
      for (const [propName, propDef] of Object.entries(obj.properties)) {
        if (moneyFields.has(propName)) {
          obj.properties[propName] = {
            $ref: '#/components/schemas/Money',
          };
        } else {
          transformProperties(propDef);
        }
      }
    }

    if (obj.items) {
      transformProperties(obj.items);
    }
    if (obj.allOf) {
      obj.allOf.forEach(transformProperties);
    }
    if (obj.anyOf) {
      obj.anyOf.forEach(transformProperties);
    }
    if (obj.oneOf) {
      obj.oneOf.forEach(transformProperties);
    }
  };

  for (const s of Object.values(schema.components.schemas)) {
    transformProperties(s);
  }

  return schema;
};
