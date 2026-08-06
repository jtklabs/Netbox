<?php
/* Test users for the throwaway SAML IdP. Attribute names deliberately mirror
 * the ones prod's Mellon exports (username, mail, givenName, sn, groups) so
 * the SP config being tested is the one prod will use. 'groups' is
 * multi-valued on purpose: it proves MellonMergeEnvVars collapses it. */
$config = array(
    'admin' => array('core:AdminPassword'),
    'example-userpass' => array(
        'exampleauth:UserPass',
        'jdoe:jdoepass' => array(
            'username'  => array('jdoe'),
            'mail'      => array('jdoe@example.com'),
            'givenName' => array('Jane'),
            'sn'        => array('Doe'),
            'groups'    => array('netbox-admins', 'netops'),
        ),
        'msmith:msmithpass' => array(
            'username'  => array('msmith'),
            'mail'      => array('msmith@example.com'),
            'givenName' => array('Mike'),
            'sn'        => array('Smith'),
            'groups'    => array('netops'),
        ),
    ),
);
