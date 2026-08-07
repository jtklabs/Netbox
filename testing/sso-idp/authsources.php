<?php
/* Test users for the throwaway SAML IdP. Attribute names are exactly the ones
 * the prod IdP sends (username, email, firstName, lastName, memberOf) so the
 * SP config being tested is the one prod uses, unmodified. 'memberOf' is
 * multi-valued on purpose: it proves MellonMergeEnvVars collapses it. */
$config = array(
    'admin' => array('core:AdminPassword'),
    'example-userpass' => array(
        'exampleauth:UserPass',
        'jdoe:jdoepass' => array(
            'username'  => array('jdoe'),
            'email'     => array('jdoe@example.com'),
            'firstName' => array('Jane'),
            'lastName'  => array('Doe'),
            'memberOf'  => array('netbox-admins', 'netops'),
        ),
        'msmith:msmithpass' => array(
            'username'  => array('msmith'),
            'email'     => array('msmith@example.com'),
            'firstName' => array('Mike'),
            'lastName'  => array('Smith'),
            'memberOf'  => array('netops'),
        ),
    ),
);
