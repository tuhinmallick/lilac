/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */

import type { Schema } from './Schema';

/**
 * The result of a select rows schema query.
 */
export type SelectRowsSchemaResult = {
    data_schema: Schema;
    alias_udf_paths?: Record<string, Array<(number | string)>>;
};
