import axios, { AxiosRequestConfig } from 'axios';

export const API_BASE = process.env.NEXT_PUBLIC_API_URL || '/api';

export const AXIOS_INSTANCE = axios.create({
  baseURL: API_BASE,
});

export const customInstance = <T>(
  config: AxiosRequestConfig,
  options?: AxiosRequestConfig,
): Promise<T> => {
  const source = axios.CancelToken.source();
  const promise = AXIOS_INSTANCE({
    ...config,
    ...options,
    cancelToken: source.token,
  }).then(({ data }) => data);

  // @ts-ignore
  promise.cancel = () => {
    source.cancel('Query was cancelled');
  };

  return promise;
};
