
import numpy as np

from sktime.base import Estimator, Model
from sktime.lagged_model_validator import LaggedModelValidator
from sktime.markovprocess import MarkovStateModel
from sktime.markovprocess._base import BayesianPosterior
from sktime.util import confidence_interval, ensure_ndarray

__author__ = 'noe, marscher'


class ChapmanKolmogorovValidator(LaggedModelValidator):
    r""" Validates a model estimated at lag time tau by testing its predictions
    for longer lag times

    Parameters
    ----------
    test_model : Model
        Model to be tested

    test_estimator : Estimator
        Parametrized Estimator that has produced the model

    memberships : ndarray(n, m)
        Set memberships to calculate set probabilities. n must be equal to
        the number of active states in model. m is the number of sets.
        memberships must be a row-stochastic matrix (the rows must sum up
        to 1).

    mlags : int or int-array, default=10
        multiples of lag times for testing the Model, e.g. range(10).
        A single int will trigger a range, i.e. mlags=10 maps to
        mlags=range(10). The setting None will choose mlags automatically
        according to the longest available trajectory
        Note that you need to be able to do a model prediction for each
        of these lag time multiples, e.g. the value 0 only make sense
        if _predict_observables(0) will work.

    conf : float, default = 0.95
        confidence interval for errors

    err_est : bool, default=False
        if the Estimator is capable of error calculation, will compute
        errors for each tau estimate. This option can be computationally
        expensive.

    """
    def __init__(self, test_model, test_estimator, memberships, mlags=None, conf=0.95,
                 err_est=False):
        self.memberships = memberships
        self.err_est = err_est
        super(ChapmanKolmogorovValidator, self).__init__(test_model, test_estimator, conf=conf, mlags=mlags)

    @property
    def memberships(self):
        return self._memberships

    @memberships.setter
    def memberships(self, value):
        self._memberships = ensure_ndarray(value, ndim=2, dtype=np.float64)
        self.n_states, self.nsets = self._memberships.shape
        assert np.allclose(self._memberships.sum(axis=1), np.ones(self.n_states))  # stochastic matrix?

    @property
    def test_model(self):
        return self._test_model

    @test_model.setter
    def test_model(self, test_model: MarkovStateModel):
        assert self.memberships is not None
        if hasattr(test_model, 'prior'):
            # todo ugly hack, cktest needs to be reworked!!
            test_model = test_model.prior
        assert self.memberships.shape[0] == test_model.n_states, 'provided memberships and test_model n_states mismatch'
        self._test_model = test_model
        # define starting distribution
        P0 = self.memberships * test_model.stationary_distribution[:, None]
        P0 /= P0.sum(axis=0)  # column-normalize
        self.P0 = P0

        active_set = test_model.count_model.active_set
        if active_set is None:
            active_set = np.arange(test_model.n_states)
        # map from the full set (here defined by the largest state index in active set) to active
        self._full2active = np.zeros(np.max(active_set) + 1, dtype=int)
        self._full2active[active_set] = np.arange(test_model.n_states)

    def _compute_observables(self, model: MarkovStateModel, mlag=1):
        # otherwise compute or predict them by model.propagate
        pk_on_set = np.zeros((self.nsets, self.nsets))
        # compute observable on prior in case for Bayesian models.
        if hasattr(model, 'prior'):
            model = model.prior
        if model.count_model is not None:
            subset = self._full2active[model.count_model.active_set]  # find subset we are now working on
        else:
            subset = None
        for i in range(self.nsets):
            p0 = self.P0[:, i]  # starting distribution on reference active set
            p0sub = p0[subset]  # map distribution to new active set
            if subset is not None:
                p0sub /= p0sub.sum()  # renormalize
            pksub = model.propagate(p0sub, mlag)
            for j in range(self.nsets):
                pk_on_set[i, j] = np.dot(pksub, self.memberships[subset, j])  # map onto set
        return pk_on_set

    # TODO: model type
    def _compute_observables_conf(self, model: BayesianPosterior, mlag=1, conf=0.95):
        # otherwise compute or predict them by model.propagate
        if model.prior.count_model is not None:
            subset = self._full2active[model.prior.count_model.active_set]  # find subset we are now working on
        else:
            subset = None
        n = self.nsets
        l = np.zeros((n, n))
        r = np.zeros_like(l)
        for i in range(n):
            p0 = self.P0[:, i]  # starting distribution
            p0sub = p0[subset]  # map distribution to new active set
            p0sub /= p0sub.sum()  # renormalize
            pksub_samples = [m.propagate(p0sub, mlag) for m in model.samples]
            for j in range(n):
                pk_on_set_samples = np.fromiter((np.dot(pksub, self.memberships[subset, j])
                                                 for pksub in pksub_samples), dtype=np.float, count=len(pksub_samples))
                l[i, j], r[i, j] = confidence_interval(pk_on_set_samples, conf=self.conf)
        return l, r


# TODO: docstring
def cktest(test_estimator, test_model, dtrajs, nsets, memberships=None, mlags=10,
           conf=0.95, err_est=False) -> ChapmanKolmogorovValidator:
    """ Conducts a Chapman-Kolmogorow test.

    Parameters
    ----------
    nsets : int
        number of sets to test on
    memberships : ndarray(n_states, nsets), optional
        optional state memberships. By default (None) will conduct a cktest
        on PCCA (metastable) sets. In case of a hidden MSM memberships are ignored.
    mlags : int or int-array, optional
        multiples of lag times for testing the Model, e.g. range(10).
        A single int will trigger a range, i.e. mlags=10 maps to
        mlags=range(10). The setting None will choose mlags automatically
        according to the longest available trajectory
    conf : float, optional
        confidence interval
    err_est : bool, optional
        compute errors also for all estimations (computationally expensive)
        If False, only the prediction will get error bars, which is often
        sufficient to validate a model.

    Returns
    -------
    cktest : :class:`ChapmanKolmogorovValidator <sktime.markovprocess.ChapmanKolmogorovValidator>`


    References
    ----------
    This test was suggested in [1]_ and described in detail in [2]_.

    .. [1] F. Noe, Ch. Schuette, E. Vanden-Eijnden, L. Reich and
        T. Weikl: Constructing the Full Ensemble of Folding Pathways
        from Short Off-Equilibrium Simulations.
        Proc. Natl. Acad. Sci. USA, 106, 19011-19016 (2009)
    .. [2] Prinz, J H, H Wu, M Sarich, B Keller, M Senne, M Held, J D
        Chodera, C Schuette and F Noe. 2011. Markov models of
        molecular kinetics: Generation and validation. J Chem Phys
        134: 174105

    """
    try:
        if memberships is None:
            pcca = test_model.pcca(nsets)
            memberships = pcca.memberships
    except NotImplementedError:
        # todo: ugh...
        memberships = np.eye(test_model.n_states)

    ck = ChapmanKolmogorovValidator(test_estimator=test_estimator, test_model=test_model, memberships=memberships,
                                    mlags=mlags, conf=conf, err_est=err_est)
    ck.fit(dtrajs)
    return ck
