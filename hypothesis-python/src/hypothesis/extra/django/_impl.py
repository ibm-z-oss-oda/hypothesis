# coding=utf-8
#
# This file is part of Hypothesis, which may be found at
# https://github.com/HypothesisWorks/hypothesis/
#
# Most of this work is copyright (C) 2013-2019 David R. MacIver
# (david@drmaciver.com), but it contains contributions by others. See
# CONTRIBUTING.rst for a full list of people who may hold copyright, and
# consult the git log if you need to determine who owns an individual
# contribution.
#
# This Source Code Form is subject to the terms of the Mozilla Public License,
# v. 2.0. If a copy of the MPL was not distributed with this file, You can
# obtain one at https://mozilla.org/MPL/2.0/.
#
# END HEADER

from __future__ import absolute_import, division, print_function

import unittest

import django.db.models as dm
import django.forms as df
import django.test as dt
from django.db import IntegrityError
from django.core.exceptions import ValidationError

import hypothesis._strategies as st
from hypothesis import reject
from hypothesis.errors import InvalidArgument
from hypothesis.extra.django._fields import from_field
from hypothesis.utils.conventions import infer

if False:
    from datetime import tzinfo  # noqa
    from typing import Any, Type, Optional, List, Text, Callable, Union  # noqa
    from hypothesis.utils.conventions import InferType  # noqa


class HypothesisTestCase(object):
    def setup_example(self):
        self._pre_setup()

    def teardown_example(self, example):
        self._post_teardown()

    def __call__(self, result=None):
        testMethod = getattr(self, self._testMethodName)
        if getattr(testMethod, u"is_hypothesis_test", False):
            return unittest.TestCase.__call__(self, result)
        else:
            return dt.SimpleTestCase.__call__(self, result)


class TestCase(HypothesisTestCase, dt.TestCase):
    pass


class TransactionTestCase(HypothesisTestCase, dt.TransactionTestCase):
    pass


@st.defines_strategy
def from_model(
    model,  # type: Type[dm.Model]
    **field_strategies  # type: Union[st.SearchStrategy[Any], InferType]
):
    # type: (...) -> st.SearchStrategy[Any]
    """Return a strategy for examples of ``model``.

    .. warning::
        Hypothesis creates saved models. This will run inside your testing
        transaction when using the test runner, but if you use the dev console
        this will leave debris in your database.

    ``model`` must be an subclass of :class:`~django:django.db.models.Model`.
    Strategies for fields may be passed as keyword arguments, for example
    ``is_staff=st.just(False)``.

    Hypothesis can often infer a strategy based the field type and validators,
    and will attempt to do so for any required fields.  No strategy will be
    inferred for an :class:`~django:django.db.models.AutoField`, nullable field,
    foreign key, or field for which a keyword
    argument is passed to ``from_model()``.  For example,
    a Shop type with a foreign key to Company could be generated with::

        shop_strategy = from_model(Shop, company=from_model(Company))

    Like for :func:`~hypothesis.strategies.builds`, you can pass
    :obj:`~hypothesis.infer` as a keyword argument to infer a strategy for
    a field which has a default value instead of using the default.
    """
    if not issubclass(model, dm.Model):
        raise InvalidArgument("model=%r must be a subtype of Model" % (model,))

    fields_by_name = {f.name: f for f in model._meta.concrete_fields}
    for name, value in sorted(field_strategies.items()):
        if value is infer:
            field_strategies[name] = from_field(fields_by_name[name])
    for name, field in sorted(fields_by_name.items()):
        if (
            name not in field_strategies
            and not field.auto_created
            and field.default is dm.fields.NOT_PROVIDED
        ):
            field_strategies[name] = from_field(field)

    for field in field_strategies:
        if model._meta.get_field(field).primary_key:
            # The primary key is generated as part of the strategy. We
            # want to find any existing row with this primary key and
            # overwrite its contents.
            kwargs = {field: field_strategies.pop(field)}
            kwargs["defaults"] = st.fixed_dictionaries(field_strategies)  # type: ignore
            return _models_impl(st.builds(model.objects.update_or_create, **kwargs))

    # The primary key is not generated as part of the strategy, so we
    # just match against any row that has the same value for all
    # fields.
    return _models_impl(st.builds(model.objects.get_or_create, **field_strategies))


@st.composite
def _models_impl(draw, strat):
    """Handle the nasty part of drawing a value for models()"""
    try:
        return draw(strat)[0]
    except IntegrityError:
        reject()


class _FormWrap:
    """Instantiating a form with data requires passing the form data
    as a dictionary (not keyword arguments) e.g. ::

        form = FormClass(data={'field1': 'value1', 'field2': 'value2'})

    ``_FormWrap`` provides a wrapper to support
    ``hypothesis.strategies.builds``.

    To wrap your ``form`` use ::

        _form = type(_, (_FormWrap,), {'wraps': form})
    """
    # TODO: is there an easier way of doing this?
    wraps = df.Form
    form_kwargs = {}

    def __new__(self, **kwargs):
        # print(kwargs)
        # print(self.form_kwargs)
        return self.wraps(data=kwargs, **self.form_kwargs)


@st.defines_strategy
def from_form(
    form,  # type: Type[dm.Model]
    **field_strategies_and_form_kwargs  # type: Union[st.SearchStrategy[Any], InferType]
):
    # type: (...) -> st.SearchStrategy[Any]
    """Return a strategy for examples of ``form``.

    ``form`` must be an subclass of :class:`~django:django.forms.Form`.
    Strategies for fields may be passed as keyword arguments, for example
    ``is_staff=st.just(False)``.

    Hypothesis can often infer a strategy based the field type and validators,
    and will attempt to do so for any required fields.  No strategy will be
  >>  inferred for an :class:`~django:django.db.forms.AutoField`, nullable field,
    foreign key, or field for which a keyword
    argument is passed to ``from_form()``.  For example,
    a Shop type with a foreign key to Company could be generated with::

        shop_strategy = from_form(Shop, company=from_form(Company))

    Like for :func:`~hypothesis.strategies.builds`, you can pass
    :obj:`~hypothesis.infer` as a keyword argument to infer a strategy for
    a field which has a default value instead of using the default.
    """
    if not issubclass(form, df.BaseForm):
        print(form.__mro__)
        raise InvalidArgument("form=%r must be a subtype of Form" % (form,))

    # Forms are a little bit different from models. Model classes have
    # all their fields defined, whereas forms may have different fields
    # per-instance. So, we ought to instantiate the form and get the
    # fields from the instance, thus we need to accept the kwargs for
    # instantiation as well as the explicitly defined strategies

    # split the form kwargs from the field strategies
    field_strategies = {}
    form_kwargs = {}
    for k, v in field_strategies_and_form_kwargs.items():
        if isinstance(v, st.SearchStrategy):
            field_strategies[k] = v
        else:
            form_kwargs[k] = v

    blank_form = form(**form_kwargs)
    fields_by_name = blank_form.fields
    for name, value in sorted(field_strategies.items()):
        if value is infer:
            field_strategies[name] = from_field(fields_by_name[name])
    for name, field in sorted(fields_by_name.items()):
        if (
            name not in field_strategies
            and not field.disabled
        ):
            field_strategies[name] = from_field(field)

    # for field in field_strategies:
    #     if form._meta.get_field(field).primary_key:
    #         # The primary key is generated as part of the strategy. We
    #         # want to find any existing row with this primary key and
    #         # overwrite its contents.
    #         kwargs = {field: field_strategies.pop(field)}
    #         kwargs["defaults"] = st.fixed_dictionaries(field_strategies)  # type: ignore
    #         return _forms_impl(st.builds(form.objects.update_or_create, **kwargs))

    # The primary key is not generated as part of the strategy, so we
    # just match against any row that has the same value for all
    # fields.
    _form = type(
        'ignored', (_FormWrap, ), {'wraps': form, 'form_kwargs': form_kwargs})
    return _forms_impl(st.builds(_form, **field_strategies))


@st.composite
def _forms_impl(draw, strat):
    """Handle the nasty part of drawing a value for from_form()"""
    try:
        return draw(strat)
    except ValidationError:
        reject()
