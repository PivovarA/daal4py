#
#*******************************************************************************
# Copyright 2020 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#******************************************************************************/

import numpy as np
import numbers
import daal4py
from scipy import sparse as sp
from sklearn.utils import check_array, check_X_y
from sklearn.linear_model import ElasticNet as ElasticNet_original
from sklearn.linear_model import Lasso as Lasso_original
from daal4py.sklearn._utils import (make2d, getFPType)

#only for compliance with Sklearn
import warnings
from sklearn.exceptions import ConvergenceWarning
from sklearn.utils.extmath import row_norms

def _daal4py_check(self, X, y_, check_input):
    #conver to 2d format
    X = make2d(X)
    y = make2d(y_)

    #convet from list type
    if isinstance(X, list):
        X = np.asarray(X, np.float64)
    if isinstance(y, list):
        y = np.asarray(y, np.float64)

    _fptype = getFPType(X)

    #check alpha
    if self.alpha == 0:
        warnings.warn("With alpha=0, this algorithm does not converge "
                      "well. You are advised to use the LinearRegression "
                      "estimator", stacklevel=2)

    #check precompute
    if isinstance(self.precompute, np.ndarray):
        if check_input:
            check_array(self.precompute, dtype=_fptype)
        self.precompute = make2d(self.precompute)
        #only for compliance with Sklearn
        if self.fit_intercept:
            X_offset = np.average(X, axis=0, weights=None)
            if self.normalize:
                X_scale = row_norms(X)
                if np.isscalar(X_scale):
                    if X_scale == .0:
                        X_scale = 1.
                elif isinstance(X_scale, np.ndarray):
                    X_scale[X_scale == 0.0] = 1.0
            else:
                X_scale = np.ones(X.shape[1], dtype=_fptype)
        else:
            X_offset = np.zeros(X.shape[1], dtype=_fptype)
            X_scale = np.ones(X.shape[1], dtype=_fptype)
        if (self.fit_intercept and not np.allclose(X_offset, np.zeros(X.shape[1])) or
                self.normalize and not np.allclose(X_scale, np.ones(X.shape[1]))):
            warnings.warn("Gram matrix was provided but X was centered"
                          " to fit intercept, "
                          "or X was normalized : recomputing Gram matrix.",
                          UserWarning)
    else:
        if self.precompute not in [False, True, 'auto']:
            raise ValueError("precompute should be one of True, False, "
                             "'auto' or array-like. Got %r" % self.precompute)
    #check X and y
    if check_input:
        X, y = check_X_y(X, y, dtype=[np.float64, np.float32], multi_output=True, y_numeric=True)
    else:
        #only for compliance with Sklearn, this assert is not required for DAAL
        if (X.flags['F_CONTIGUOUS'] == False):
            raise ValueError("ndarray is not Fortran contiguous")

    #check selection
    if self.selection not in ['random', 'cyclic']:
        raise ValueError("selection should be either random or cyclic.")

    return X, y

def _daal4py_fit_enet(self, X, y_, check_input):

    #appropriate checks
    X, y = _daal4py_check(self, X, y_, check_input)
    _fptype = getFPType(X)

    penalty_L1 = np.asarray(self.alpha*self.l1_ratio, dtype=X.dtype)
    penalty_L2 = np.asarray(self.alpha*(1.0 - self.l1_ratio), dtype=X.dtype)
    if (penalty_L1.size != 1 or penalty_L2.size != 1):
        raise ValueError("alpha or l1_ratio length is wrong")
    penalty_L1 = penalty_L1.reshape((1,-1))
    penalty_L2 = penalty_L2.reshape((1,-1))

    mse_alg = daal4py.optimization_solver_mse(
        numberOfTerms = X.shape[0],
        fptype = _fptype,
        method = 'defaultDense'
    )
    mse_alg.setup(X, y, None)

    cd_solver = daal4py.optimization_solver_coordinate_descent(
        function = mse_alg,
        fptype = _fptype,
        method = 'defaultDense',
        selection = self.selection,
        seed = 0 if (self.random_state == None) else self.random_state,
        nIterations = self.max_iter,
        positive = self.positive,
        accuracyThreshold = self.tol
    )

    #set warm_start
    if (self.warm_start and hasattr(self, "coef_") and isinstance(self.coef_, np.ndarray)):
        n_rows = y.shape[1]
        n_cols = X.shape[1] + 1
        inputArgument = np.zeros((n_rows, n_cols), dtype = _fptype)
        for i in range(n_rows):
            inputArgument[i][0] = self.intercept_ if (n_rows == 1) else self.intercept_[i]
            inputArgument[i][1:] = self.coef_[:].copy(order='C') if (n_rows == 1) else self.coef_[i,:].copy(order='C')
        cd_solver.setup(inputArgument)

    elastic_net_alg = daal4py.elastic_net_training(
        fptype = _fptype,
        method = 'defaultDense',
        interceptFlag = (self.fit_intercept is True),
        dataUseInComputation = 'doUse' if (self.copy_X == False) else 'doNotUse',
        penaltyL1 = penalty_L1,
        penaltyL2 = penalty_L2,
        optimizationSolver = cd_solver
    )
    try:
        if isinstance(self.precompute, np.ndarray):
            elastic_net_res = elastic_net_alg.compute(data=X, dependentVariables=y, gramMatrix=self.precompute)
        else:
            elastic_net_res = elastic_net_alg.compute(data=X, dependentVariables=y)
    except RuntimeError:
        return None

    #set coef_ and intersept_ results
    elastic_net_model = elastic_net_res.model
    self.daal_model_ = elastic_net_model
    coefs = elastic_net_model.Beta

    self.intercept_ = coefs[:,0].copy(order='C')
    self.coef_ = coefs[:,1:].copy(order='C')

    #only for compliance with Sklearn
    if y.shape[1] == 1:
        self.coef_ = np.ravel(self.coef_)
    self.intercept_ = np.ravel(self.intercept_)
    if self.intercept_.shape[0] == 1:
        self.intercept_ = self.intercept_[0]

    #set n_iter_
    n_iter = cd_solver.__get_result__().nIterations[0][0]
    if y.shape[1] == 1:
        self.n_iter_ = n_iter
    else:
        self.n_iter_ = np.full(y.shape[1], n_iter)

    #only for compliance with Sklearn
    if (self.max_iter == n_iter + 1):
        warnings.warn("Objective did not converge. You might want to "
                      "increase the number of iterations.", ConvergenceWarning)

    #only for dual_gap computation, it is not required for DAAL
    self.X = X
    self.y = y

    return self

def _daal4py_predict_enet(self, X):
    X = make2d(X)
    _fptype = getFPType(self.coef_)

    elastic_net_palg = daal4py.elastic_net_prediction(
        fptype=_fptype,
        method='defaultDense'
    )
    elastic_net_res = elastic_net_palg.compute(X, self.daal_model_)

    res = elastic_net_res.prediction

    if res.shape[1] == 1 and self.coef_.ndim == 1:
        res = np.ravel(res)
    return res

def _daal4py_fit_lasso(self, X, y_, check_input):

    #appropriate checks
    X, y = _daal4py_check(self, X, y_, check_input)
    _fptype = getFPType(X)

    mse_alg = daal4py.optimization_solver_mse(
        numberOfTerms = X.shape[0],
        fptype = _fptype,
        method = 'defaultDense'
    )
    mse_alg.setup(X, y, None)

    cd_solver = daal4py.optimization_solver_coordinate_descent(
        function = mse_alg,
        fptype = _fptype,
        method = 'defaultDense',
        selection = self.selection,
        seed = 0 if (self.random_state == None) else self.random_state,
        nIterations = self.max_iter,
        positive = self.positive,
        accuracyThreshold = self.tol
    )

    #set warm_start
    if (self.warm_start and hasattr(self, "coef_") and isinstance(self.coef_, np.ndarray)):
        n_rows = y.shape[1]
        n_cols = X.shape[1] + 1
        inputArgument = np.zeros((n_rows, n_cols), dtype = _fptype)
        for i in range(n_rows):
            inputArgument[i][0] = self.intercept_ if (n_rows == 1) else self.intercept_[i]
            inputArgument[i][1:] = self.coef_[:].copy(order='C') if (n_rows == 1) else self.coef_[i,:].copy(order='C')
        cd_solver.setup(inputArgument)

    lasso_alg = daal4py.lasso_regression_training(
        fptype = _fptype,
        method = 'defaultDense',
        interceptFlag = (self.fit_intercept is True),
        dataUseInComputation = 'doUse' if (self.copy_X == False) else 'doNotUse',
        lassoParameters = self.alpha,
        optimizationSolver = cd_solver
    )
    try:
        if isinstance(self.precompute, np.ndarray):
            lasso_res = lasso_alg.compute(data=X, dependentVariables=y, gramMatrix=self.precompute)
        else:
            lasso_res = lasso_alg.compute(data=X, dependentVariables=y)
    except RuntimeError:
        return None

    #set coef_ and intersept_ results
    lasso_model = lasso_res.model
    self.daal_model_ = lasso_model
    coefs = lasso_model.Beta

    self.intercept_ = coefs[:,0].copy(order='C')
    self.coef_ = coefs[:,1:].copy(order='C')

    #only for compliance with Sklearn
    if y.shape[1] == 1:
        self.coef_ = np.ravel(self.coef_)
    self.intercept_ = np.ravel(self.intercept_)
    if self.intercept_.shape[0] == 1:
        self.intercept_ = self.intercept_[0]

    #set n_iter_
    n_iter = cd_solver.__get_result__().nIterations[0][0]
    if y.shape[1] == 1:
        self.n_iter_ = n_iter
    else:
        self.n_iter_ = np.full(y.shape[1], n_iter)

    #only for compliance with Sklearn
    if (self.max_iter == n_iter + 1):
        warnings.warn("Objective did not converge. You might want to "
                      "increase the number of iterations.", ConvergenceWarning)

    #only for dual_gap computation, it is not required for DAAL
    self.X = X
    self.y = y

    return self

def _daal4py_predict_lasso(self, X):
    X = make2d(X)
    _fptype = getFPType(self.coef_)

    lasso_palg = daal4py.lasso_regression_prediction(
        fptype=_fptype,
        method='defaultDense'
    )
    lasso_res = lasso_palg.compute(X, self.daal_model_)

    res = lasso_res.prediction

    if res.shape[1] == 1 and self.coef_.ndim == 1:
        res = np.ravel(res)
    return res

class ElasticNet(ElasticNet_original):
    __doc__ = ElasticNet_original.__doc__

    def __init__(self, alpha=1.0, l1_ratio=0.5, fit_intercept=True,
                 normalize=False, precompute=False, max_iter=1000,
                 copy_X=True, tol=1e-4, warm_start=False, positive=False,
                 random_state=None, selection='cyclic'):
        super(ElasticNet, self).__init__(
            alpha=alpha, l1_ratio=l1_ratio, fit_intercept=fit_intercept,
            normalize=normalize, precompute=precompute, max_iter=max_iter,
            copy_X=copy_X, tol=tol, warm_start=warm_start,
            positive=positive, random_state=random_state, selection=selection)
        self.__gap = None

    def fit(self, X, y, check_input=True):
        """Fit model with coordinate descent.

        Parameters
        ----------
        X : ndarray or scipy.sparse matrix, (n_samples, n_features)
            Data

        y : ndarray, shape (n_samples,) or (n_samples, n_targets)
            Target. Will be cast to X's dtype if necessary

        check_input : boolean, (default=True)
            Allow to bypass several input checking.
            Don't use this parameter unless you know what you do.

        Notes
        -----

        Coordinate descent is an algorithm that considers each column of
        data at a time hence it will automatically convert the X input
        as a Fortran-contiguous numpy array if necessary.

        To avoid memory re-allocation it is advised to allocate the
        initial data in memory directly using that format.
        """

        if isinstance(X, list):
            self.fit_shape_good_for_daal_ = True if (len(X) >= len(X[0])) else False
        else:
            self.fit_shape_good_for_daal_ = True if (X.shape[0] >= X.shape[1]) else False
        if (sp.issparse(X) or
                not self.fit_shape_good_for_daal_ or
                not (isinstance(X, list) or X.dtype == np.float64 or X.dtype == np.float32)):
            if hasattr(self, 'daal_model_'):
                del self.daal_model_
            return super(ElasticNet, self).fit(X, y, check_input=check_input)
        else:
            self.n_iter_ = None
            self.__gap = None
            res = _daal4py_fit_enet(self, X, y, check_input=check_input)
            if res is None:
                if hasattr(self, 'daal_model_'):
                    del self.daal_model_
                return super(ElasticNet, self).fit(X, y, check_input=check_input)
            else:
                return res


    def predict(self, X):
        """Predict using the linear model

        Parameters
        ----------
        X : array-like or sparse matrix, shape = (n_samples, n_features)
            Samples.

        Returns
        -------
        C : array, shape = (n_samples,)
            Returns predicted values.
        """

        X = check_array(X, accept_sparse=['csr', 'csc', 'coo'])
        good_shape_for_daal = True if X.ndim <= 1 else True if X.shape[0] >= X.shape[1] else False

        if (not hasattr(self, 'daal_model_') or
                sp.issparse(X) or
                not good_shape_for_daal or
                not (X.dtype == np.float64 or X.dtype == np.float32)):
            return self._decision_function(X)
        else:
            return _daal4py_predict_enet(self, X)


    @property
    def dual_gap_(self):
        if (self.__gap is None):
            l1_reg = np.asarray(self.alpha*self.l1_ratio, dtype=getFPType(self.X)) * self.X.shape[0]
            l2_reg = np.asarray(self.alpha*(1.0 - self.l1_ratio), dtype=getFPType(self.X)) * self.X.shape[0]
            l1_reg = l2_reg.reshape((1,-1))
            l2_reg = l2_reg.reshape((1,-1))
            n_targets = self.y.shape[1]

            if (n_targets == 1):
                self.__gap = self.tol + 1.0
                X_offset = np.average(self.X, axis=0)
                y_offset = np.average(self.y, axis=0)
                coef = np.reshape(self.coef_, (self.coef_.shape[0], 1))
                R = (self.y - y_offset) - np.dot((self.X - X_offset), coef)
                XtA = np.dot((self.X - X_offset).T, R) - l2_reg * coef
                R_norm2 = np.dot(R.T, R)
                coef_norm2 = np.dot(self.coef_, self.coef_)
                dual_norm_XtA = np.max(XtA) if self.positive else np.max(np.abs(XtA))
                if dual_norm_XtA > l1_reg:
                    const = l1_reg / dual_norm_XtA
                    A_norm2 = R_norm2 * (const ** 2)
                    self.__gap = 0.5 * (R_norm2 + A_norm2)
                else:
                    const = 1.0
                    self.__gap = R_norm2
                l1_norm = np.sum(np.abs(self.coef_))
                self.__gap += (l1_reg * l1_norm - const * np.dot(R.T, (self.y - y_offset)) + 0.5 * l2_reg * (1 + const ** 2) * coef_norm2)
                self.__gap = self.__gap[0][0]
            else:
                self.__gap = np.full(n_targets, self.tol + 1.0)
                X_offset = np.average(self.X, axis=0)
                y_offset = np.average(self.y, axis=0)
                for k in range(n_targets):
                    R = (self.y[:, k] - y_offset[k]) - np.dot((self.X - X_offset), self.coef_[k, :].T)
                    XtA = np.dot((self.X - X_offset).T, R) - l2_reg * self.coef_[k, :].T
                    R_norm2 = np.dot(R.T, R)
                    coef_norm2 = np.dot(self.coef_[k, :], self.coef_[k, :].T)
                    dual_norm_XtA = np.max(XtA) if self.positive else np.max(np.abs(XtA))
                    if dual_norm_XtA > l1_reg:
                        const = l1_reg / dual_norm_XtA
                        A_norm2 = R_norm2 * (const ** 2)
                        self.__gap[k] = 0.5 * (R_norm2 + A_norm2)
                    else:
                        const = 1.0
                        self.__gap[k] = R_norm2
                    l1_norm = np.sum(np.abs(self.coef_[k, :]))
                    self.__gap[k] += (l1_reg * l1_norm - const * np.dot(R.T, (self.y[:, k] - y_offset[k])) + 0.5 * l2_reg * (1 + const ** 2) * coef_norm2)
        return self.__gap

    @dual_gap_.setter
    def dual_gap_(self, value):
        self.__gap = value

    @dual_gap_.deleter
    def dual_gap_(self):
        self.__gap = None

class Lasso(ElasticNet):
    __doc__ = Lasso_original.__doc__

    def __init__(self, alpha=1.0, fit_intercept=True, normalize=False,
                 precompute=False, copy_X=True, max_iter=1000,
                 tol=1e-4, warm_start=False, positive=False,
                 random_state=None, selection='cyclic'):
        super().__init__(
            alpha=alpha, l1_ratio=1.0, fit_intercept=fit_intercept,
            normalize=normalize, precompute=precompute, copy_X=copy_X,
            max_iter=max_iter, tol=tol, warm_start=warm_start,
            positive=positive, random_state=random_state,
            selection=selection)
        self.__gap = None

    def fit(self, X, y, check_input=True):
        """Fit model with coordinate descent.

        Parameters
        ----------
        X : ndarray or scipy.sparse matrix, (n_samples, n_features)
            Data

        y : ndarray, shape (n_samples,) or (n_samples, n_targets)
            Target. Will be cast to X's dtype if necessary

        check_input : boolean, (default=True)
            Allow to bypass several input checking.
            Don't use this parameter unless you know what you do.

        Notes
        -----

        Coordinate descent is an algorithm that considers each column of
        data at a time hence it will automatically convert the X input
        as a Fortran-contiguous numpy array if necessary.

        To avoid memory re-allocation it is advised to allocate the
        initial data in memory directly using that format.
        """

        if isinstance(X, list):
            self.fit_shape_good_for_daal_ = True if (len(X) >= len(X[0])) else False
        else:
            self.fit_shape_good_for_daal_ = True if (X.shape[0] >= X.shape[1]) else False
        if (sp.issparse(X) or
                not self.fit_shape_good_for_daal_ or
                not (isinstance(X, list) or X.dtype == np.float64 or X.dtype == np.float32)):
            if hasattr(self, 'daal_model_'):
                del self.daal_model_
            return super(ElasticNet, self).fit(X, y, check_input=check_input)
        else:
            self.n_iter_ = None
            self.__gap = None
            res = _daal4py_fit_lasso(self, X, y, check_input=check_input)
            if res is None:
                if hasattr(self, 'daal_model_'):
                    del self.daal_model_
                return super(ElasticNet, self).fit(X, y, check_input=check_input)
            else:
                return res


    def predict(self, X):
        """Predict using the linear model

        Parameters
        ----------
        X : array-like or sparse matrix, shape = (n_samples, n_features)
            Samples.

        Returns
        -------
        C : array, shape = (n_samples,)
            Returns predicted values.
        """

        X = check_array(X, accept_sparse=['csr', 'csc', 'coo'])
        good_shape_for_daal = True if X.ndim <= 1 else True if X.shape[0] >= X.shape[1] else False

        if (not hasattr(self, 'daal_model_') or
                sp.issparse(X) or
                not good_shape_for_daal or
                not (X.dtype == np.float64 or X.dtype == np.float32)):
            return self._decision_function(X)
        else:
            return _daal4py_predict_lasso(self, X)
